import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { IntegrityCheck, IntegrityReport } from '../../api/types'
import { IntegrityPanel, sortChecks } from './IntegrityPanel'

const check = (over: Partial<IntegrityCheck> = {}): IntegrityCheck => ({ id: 'c', group: 'Money', title: 'A check', status: 'passed', reason: 'It held.', evaluated: 1, flagged: 0, examples: [], ...over })
const report = (checks: IntegrityCheck[]): IntegrityReport => ({
  summary: { passed: checks.filter((c) => c.status === 'passed').length, warning: checks.filter((c) => c.status === 'warning').length, failed: checks.filter((c) => c.status === 'failed').length, not_evaluated: checks.filter((c) => c.status === 'not_evaluated').length },
  checks,
})
const ALL = [
  check({ id: 'ok', title: 'Fine', status: 'passed' }),
  check({ id: 'nope', title: 'Not enough data', status: 'not_evaluated', reason: 'No pay run was converted together with the paychecks.' }),
  check({ id: 'warn', title: 'Looks off', status: 'warning', reason: 'One paycheck differs.', flagged: 3, examples: [{ record: 'paycheck V1', detail: 'earnings add up to 9, gross pay is 10' }] }),
  check({ id: 'bad', title: 'Broken', status: 'failed', reason: 'A record id is used twice.', flagged: 1, examples: [{ record: 'employee E1', detail: 'appears 2 times with different content' }] }),
]

describe('IntegrityPanel', () => {
  it('shows all four statuses with their counts, in words and not only in colour', () => {
    render(<IntegrityPanel report={report(ALL)} />)
    const summary = screen.getByTestId('integrity-summary')
    for (const [status, label] of [['passed', 'Passed'], ['warning', 'Warning'], ['failed', 'Failed'], ['not_evaluated', 'Not evaluated']] as const) {
      expect(within(summary).getByText(label, { exact: false })).toBeInTheDocument()
      expect(screen.getByTestId(`integrity-count-${status}`)).toHaveTextContent('1')
    }
  })

  it('lists failed checks first, then warnings, passed and not evaluated, keeping the engine order within a status', () => {
    const more = [...ALL, check({ id: 'ok2', title: 'Second pass', status: 'passed' })]
    expect(sortChecks(more).map((c) => c.id)).toEqual(['bad', 'warn', 'ok', 'ok2', 'nope'])
    render(<IntegrityPanel report={report(more)} />)
    const ids = screen.getAllByRole('listitem').map((li) => li.getAttribute('data-testid')).filter((t) => t?.startsWith('check-') && !t.startsWith('check-status'))
    expect(ids).toEqual(['check-bad', 'check-warn', 'check-ok', 'check-ok2', 'check-nope'])
  })

  it('says why a check was not evaluated, and states each check’s status next to its title', () => {
    render(<IntegrityPanel report={report(ALL)} />)
    const nope = screen.getByTestId('check-nope')
    expect(within(nope).getByTestId('check-status-nope')).toHaveTextContent('Not evaluated')
    expect(nope).toHaveTextContent('No pay run was converted together with the paychecks.')
    expect(screen.getByTestId('check-status-bad')).toHaveTextContent('Failed')
    expect(screen.getByTestId('check-bad')).toHaveTextContent('Money')
  })

  it('lets you open the affected records, and says when only some are shown', () => {
    render(<IntegrityPanel report={report(ALL)} />)
    const warn = screen.getByTestId('check-warn')
    expect(within(warn).getByText('Show 1 of 3 affected')).toBeInTheDocument()
    expect(warn).toHaveTextContent('paycheck V1: earnings add up to 9, gross pay is 10')
    expect(within(screen.getByTestId('check-bad')).getByText('Show the 1 affected')).toBeInTheDocument()
    expect(within(screen.getByTestId('check-ok')).queryByText(/affected/)).not.toBeInTheDocument()
  })

  it('explains that nothing is guessed and nothing is changed', () => {
    render(<IntegrityPanel report={report([])} />)
    expect(screen.getByTestId('integrity-panel')).toHaveTextContent('Nothing is guessed')
    expect(screen.getByTestId('integrity-panel')).toHaveTextContent('nothing here changes your data')
    expect(screen.getByTestId('integrity-count-failed')).toHaveTextContent('0')
  })
})

describe('IntegrityPanel as a dashboard', () => {
  const MIX = [
    check({ id: 'a', group: 'Identity', title: 'A', status: 'passed' }),
    check({ id: 'b', group: 'Dates', title: 'B', status: 'not_evaluated', reason: 'No record has both dates.' }),
    check({ id: 'c', group: 'Money', title: 'C', status: 'warning', reason: 'One differs.', flagged: 1, examples: [{ record: 'paycheck V1', detail: 'x' }] }),
    check({ id: 'd', group: 'Identity', title: 'D', status: 'failed', reason: 'Used twice.', flagged: 1 }),
  ]
  it('groups checks by their own categories, worst category first, with a per-category summary', () => {
    render(<IntegrityPanel report={report(MIX)} />)
    const groups = screen.getAllByTestId(/^integrity-group-/).map((g) => g.getAttribute('data-testid'))
    expect(groups).toEqual(['integrity-group-Identity', 'integrity-group-Money', 'integrity-group-Dates'])
    expect(within(screen.getByTestId('integrity-group-Identity')).getByText(/1 failed/)).toBeInTheDocument()
    expect(within(screen.getByTestId('integrity-group-Identity')).getAllByRole('listitem').map((li) => li.getAttribute('data-testid'))).toEqual(['check-d', 'check-a'])
  })
  it('never invents a category: only the ones the checks carry exist', () => {
    render(<IntegrityPanel report={report([check({ id: 'x', group: 'Identity' })])} />)
    expect(screen.getAllByTestId(/^integrity-group-/)).toHaveLength(1)
  })
  it('keeps warnings, failures and not-evaluated checks visible, and points to them', () => {
    render(<IntegrityPanel report={report(MIX)} />)
    expect(screen.getByTestId('integrity-attention')).toHaveTextContent('2 checks need your attention')
    expect(screen.getByTestId('check-status-b')).toHaveTextContent('Not evaluated')
    expect(screen.getByTestId('check-b')).toHaveTextContent('No record has both dates.')
    expect(screen.getByTestId('check-c')).toHaveClass('bg-amber-50/60')
    expect(screen.getByTestId('check-d')).toHaveClass('bg-rose-50/60')
    expect(screen.getByTestId('check-a')).not.toHaveClass('bg-amber-50/60')
  })
  it('shows no attention banner when nothing is flagged', () => {
    render(<IntegrityPanel report={report([check({ id: 'ok' }), check({ id: 'n', status: 'not_evaluated' })])} />)
    expect(screen.queryByTestId('integrity-attention')).not.toBeInTheDocument()
  })
  it('says what a passing check does and does not establish', () => {
    render(<IntegrityPanel report={report([check({})])} />)
    expect(screen.getByRole('heading', { name: 'Data quality & integrity' })).toBeInTheDocument()
    expect(screen.getByTestId('integrity-caveat')).toHaveTextContent('consistent with each other, not that they are correct')
    expect(screen.getByTestId('integrity-caveat')).toHaveTextContent('cannot verify who owns records whose employee you assigned yourself')
  })
  it('counts every status from the checks and draws one bar segment per non-empty status', () => {
    const { container } = render(<IntegrityPanel report={report(MIX)} />)
    for (const s of ['passed', 'warning', 'failed', 'not_evaluated']) expect(screen.getByTestId(`integrity-count-${s}`)).toHaveTextContent('1')
    expect(container.querySelectorAll('[aria-hidden].flex.h-2 > span')).toHaveLength(4)
  })
})
