import operator
from typing import Annotated, List

from ai_prompter import Prompter
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from loguru import logger
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.domain.content_settings import ContentSettings
from open_notebook.domain.notebook import vector_search
from open_notebook.exceptions import ExternalServiceError, OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content

# Output budget shared by the user-visible Ask stages (strategy, final
# synthesis). Matches chat and transformations. The previous 2000 cap
# silently truncated answers in token-dense languages and left reasoning
# models with no budget for the visible answer after their thinking (#1221).
# The per-search extraction stage has its own, smaller budget below.
ASK_MAX_TOKENS = 8192

# Ask pipeline tuning knobs, admin-editable via Settings (ContentSettings).
# These defaults apply when settings are unset or unreadable; the graph
# clamps stored values to safe ranges (one may predate validation).
#
# ASK_MAX_SEARCHES is the main credit-conservation lever: every search fans
# out into its own model call that carries the retrieved chunks into
# context, so the cap bounds both the call count and the input tokens spent
# per question.
ASK_MAX_SEARCHES = 3
# Hard ceiling for the search cap regardless of settings — keeps the
# per-question fan-out multiplier bounded.
ASK_MAX_SEARCHES_CEILING = 5
# Output budget for each per-search extraction answer. That answer is
# intermediate material for the final synthesis, not user-visible prose, so
# it needs far less headroom than ASK_MAX_TOKENS.
ASK_SEARCH_ANSWER_MAX_TOKENS = 2048
ASK_SEARCH_ANSWER_MIN_TOKENS = 256

# Clarifying-question cap for the Ask-tab clarification gate. Blocking: when
# the strategy returns clarifications, searches are deliberately skipped.
MAX_CLARIFICATIONS = 3


async def _ask_limits() -> tuple[int, int]:
    """Resolve the (max_searches, search_answer_max_tokens) tuning knobs.

    Reads the admin-editable ContentSettings, falling back to the module
    defaults when settings are unreadable and clamping stored values to safe
    ranges. Follows the graphs/source.py pattern of degrading to defaults
    rather than failing the turn on a settings hiccup.
    """
    max_searches = ASK_MAX_SEARCHES
    search_answer_max_tokens = ASK_SEARCH_ANSWER_MAX_TOKENS
    try:
        settings: ContentSettings = await ContentSettings.get_instance()  # type: ignore[assignment]
        if settings.ask_max_searches is not None:
            max_searches = max(
                1, min(ASK_MAX_SEARCHES_CEILING, settings.ask_max_searches)
            )
        if settings.ask_search_answer_max_tokens is not None:
            search_answer_max_tokens = max(
                ASK_SEARCH_ANSWER_MIN_TOKENS,
                min(ASK_MAX_TOKENS, settings.ask_search_answer_max_tokens),
            )
    except Exception as e:
        logger.warning(f"Failed to load Ask settings, using defaults: {e}")
    return max_searches, search_answer_max_tokens


class SubGraphState(TypedDict):
    question: str
    term: str
    instructions: str
    results: dict
    answer: str
    ids: list  # Added for provide_answer function
    notebook_ids: list  # Notebook scope forwarded from ThreadState (#574, #87)
    clarifications: list  # Clarifying questions for the clarify branch


class Search(BaseModel):
    term: str
    instructions: str = Field(
        description="Tell the answeting LLM what information you need extracted from this search"
    )


class Strategy(BaseModel):
    reasoning: str
    searches: List[Search] = Field(
        default_factory=list,
        description=(
            "Searches to run in parallel, most relevant first. The server "
            "applies a hard cap from Ask settings (default 3)."
        ),
    )
    clarifications: List[str] = Field(
        default_factory=list,
        description=(
            "1-3 clarifying questions to ask the user when the question is "
            "missing decision-critical context. Mutually exclusive with "
            "searches: when clarifications are present, searches must be empty."
        ),
    )


class ThreadState(TypedDict):
    question: str
    strategy: Strategy
    answers: Annotated[list, operator.add]
    final_answer: str
    # Optional notebook scope: when non-empty, every search the strategy fans
    # out runs only against sources/notes linked to these notebooks (#574, #87).
    notebook_ids: list
    # Optional conversation history (rendered "User:/Assistant:" turns) so the
    # home chat can answer follow-ups. Never set by the standalone ask
    # endpoint — the prompts only render the section when it's non-empty.
    chat_history: str
    # Opt-in clarification gate. Only the standalone Ask endpoint sets this;
    # the home chat reuses this graph without it, so its behavior is
    # unchanged. When true, a strategy carrying clarifications short-circuits
    # to the clarify node instead of running any searches (blocking).
    clarify: bool


async def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        parser: PydanticOutputParser[Strategy] = PydanticOutputParser(
            pydantic_object=Strategy
        )
        system_prompt = Prompter(prompt_template="ask/entry", parser=parser).render(  # type: ignore[arg-type]
            data=state  # type: ignore[arg-type]
        )
        model = await provision_langchain_model(
            system_prompt,
            config.get("configurable", {}).get("strategy_model"),
            "tools",
            max_tokens=ASK_MAX_TOKENS,
            structured=dict(type="json"),
        )
        # model = model.bind_tools(tools)
        # First get the raw response from the model
        ai_message = await model.ainvoke(system_prompt)

        # Clean the thinking content from the response
        message_content = extract_text_content(ai_message.content)
        cleaned_content = clean_thinking_content(message_content)

        # Parse the cleaned JSON content
        strategy = parser.parse(cleaned_content)

        # Normalise the clarification gate: drop blank questions and cap the
        # list. When the gate is opted into (standalone Ask), clarifications
        # are blocking — clear the searches so none run. When it is not
        # opted into (home chat reuses this node), strip any clarifications
        # so its behavior is provably unchanged.
        strategy.clarifications = [
            question.strip() for question in strategy.clarifications if question.strip()
        ][:MAX_CLARIFICATIONS]
        if state.get("clarify"):
            if strategy.clarifications:
                strategy.searches = []
        else:
            strategy.clarifications = []

        # A reasoning model that spends its whole budget thinking returns a
        # syntactically valid strategy with blank search terms. Drop those and
        # fail loudly when nothing usable remains, instead of running empty
        # vector searches and answering "no documents found".
        strategy.searches = [s for s in strategy.searches if s.term.strip()]
        # Hard cap on the fan-out, from Ask settings (default ASK_MAX_SEARCHES).
        # Most-relevant-first ordering means truncation drops the tail.
        max_searches, _ = await _ask_limits()
        strategy.searches = strategy.searches[:max_searches]
        if not strategy.searches and not strategy.clarifications:
            raise ExternalServiceError(
                "The strategy model returned no search terms or clarifying "
                "questions for this question. "
                "This usually means the model spent its output budget on reasoning "
                "or returned an empty response. Pick a different strategy model in "
                "the Ask page's advanced model options, or rephrase the question."
            )

        return {"strategy": strategy}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


async def trigger_queries(state: ThreadState, config: RunnableConfig):
    # Clarification gate (blocking): when the strategy asks clarifying
    # questions, searches were deliberately cleared — route straight to the
    # clarify node and never fan out search work.
    if state["strategy"].clarifications:
        return [
            Send(
                "clarify",
                {
                    "question": state["question"],
                    "clarifications": state["strategy"].clarifications,
                },
            )
        ]
    return [
        Send(
            "provide_answer",
            {
                "question": state["question"],
                "instructions": s.instructions,
                "term": s.term,
                "notebook_ids": state.get("notebook_ids") or [],
                # "type": s.type,
            },
        )
        for s in state["strategy"].searches
    ]


async def clarify(state: SubGraphState, config: RunnableConfig) -> dict:
    """Render the strategy's clarifying questions as the final answer.

    Deliberately deterministic — no model call. Blocking by design: this
    branch is only reachable when the strategy carried clarifications, which
    means the searches were already cleared in the strategy node.
    """
    questions = "\n".join(
        f"{index}. {question}"
        for index, question in enumerate(state["clarifications"], start=1)
    )
    final_answer = (
        f"Before I can answer this well, I need a bit more information:\n\n{questions}"
    )
    return {"final_answer": final_answer}


async def provide_answer(state: SubGraphState, config: RunnableConfig) -> dict:
    try:
        payload = state
        # if state["type"] == "text":
        #     results = text_search(state["term"], 10, True, True)
        # else:
        # T5: an explicitly-empty notebook scope means "this caller may read
        # nothing" — it must produce no results, never silently widen to a
        # global search. (The search router short-circuits before this, but
        # the graph is reachable from other entry points, so the guard
        # belongs here too. A scope that is ABSENT keeps the legacy global
        # behavior for callers that predate scoping.)
        if "notebook_ids" in state and not state["notebook_ids"]:
            return {"answers": []}
        results = await vector_search(
            state["term"],
            10,
            True,
            True,
            notebook_ids=state.get("notebook_ids") or None,
        )
        if len(results) == 0:
            return {"answers": []}
        payload["results"] = results
        ids = [r["id"] for r in results]
        payload["ids"] = ids
        system_prompt = Prompter(prompt_template="ask/query_process").render(data=payload)  # type: ignore[arg-type]
        _, search_answer_max_tokens = await _ask_limits()
        model = await provision_langchain_model(
            system_prompt,
            config.get("configurable", {}).get("answer_model"),
            "tools",
            max_tokens=search_answer_max_tokens,
        )
        ai_message = await model.ainvoke(system_prompt)
        ai_content = clean_thinking_content(extract_text_content(ai_message.content))
        if not ai_content.strip():
            # Nothing left after stripping thinking content — an empty partial
            # answer only pollutes the final synthesis.
            return {"answers": []}
        return {"answers": [ai_content]}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


async def write_final_answer(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        system_prompt = Prompter(prompt_template="ask/final_answer").render(data=state)  # type: ignore[arg-type]
        model = await provision_langchain_model(
            system_prompt,
            config.get("configurable", {}).get("final_answer_model"),
            "tools",
            max_tokens=ASK_MAX_TOKENS,
        )
        ai_message = await model.ainvoke(system_prompt)
        final_content = extract_text_content(ai_message.content)
        return {"final_answer": clean_thinking_content(final_content)}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_node("provide_answer", provide_answer)
agent_state.add_node("clarify", clarify)
agent_state.add_node("write_final_answer", write_final_answer)
agent_state.add_edge(START, "agent")
agent_state.add_conditional_edges(
    "agent", trigger_queries, ["provide_answer", "clarify"]
)
agent_state.add_edge("provide_answer", "write_final_answer")
agent_state.add_edge("clarify", END)
agent_state.add_edge("write_final_answer", END)

graph = agent_state.compile()
