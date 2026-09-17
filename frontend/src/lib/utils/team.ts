/**
 * Team slug → prototype pill variant (issue #5/T4). Unknown slugs get the
 * neutral status chip so a newly created team never renders unstyled.
 */
export function teamPillVariant(
  slug: string
): 'hr' | 'finance' | 'executive' | 'company-shared' | 'status' {
  if (slug === 'hr') return 'hr'
  if (slug === 'finance') return 'finance'
  if (slug === 'executive') return 'executive'
  if (slug === 'company_shared') return 'company-shared'
  return 'status'
}
