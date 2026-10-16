import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Issue, UnitResult } from '../../api/types'
import { storyFor } from '../../lib/results'
import { record, repoSnapshot } from '../../test/utils'
import { RecordSummary } from './RecordSummary'
import { StoryPanel } from './StoryPanel'

const issue = (code: string, severity: Issue['severity'] = 'info', path: string | null = null): Issue => ({ severity, code, message: `server says ${code}`, path, source_id: null })
const unit = (over: Partial<UnitResult> = {}): UnitResult => ({ file: 'f.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: { employee: 1 }, issues: [], records: [record()], traces: [], ...over })

describe('RecordSummary', () => {
  const employees = repoSnapshot('prismhr-employee').records

  it('shows one record in plain words, with sections and a heading, and no picker', () => {
    render(<RecordSummary records={[employees[0]!]} />)
    expect(screen.getByTestId('record-heading')).toHaveTextContent('Jordan Q Rivera')
    for (const title of ['Who this is', 'Employment', 'Pay set-up']) expect(screen.getByRole('region', { name: title })).toBeInTheDocument()
    expect(screen.getByText('Last 4 digits only: 6789')).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('switches between a few records with tabs', async () => {
    render(<RecordSummary records={employees} />)
    expect(screen.getAllByRole('tab')).toHaveLength(2)
    expect(screen.getByRole('tab', { name: /Jordan/ })).toHaveAttribute('aria-selected', 'true')
    await userEvent.setup().click(screen.getByRole('tab', { name: /Casey/ }))
    expect(screen.getByTestId('record-heading')).toHaveTextContent('Casey Nguyen')
    expect(screen.getByText('Terminated')).toBeInTheDocument()
  })

  it('uses a list for many records and says where you are', async () => {
    const many = Array.from({ length: 12 }, (_, i) => record({ source_id: `E${i}`, first_name: `Person${i}`, last_name: 'Test' }))
    render(<RecordSummary records={many} />)
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    const picker = screen.getByRole('combobox', { name: 'Choose a converted record' })
    expect(screen.getByText('(1 of 12)')).toBeInTheDocument()
    await userEvent.setup().selectOptions(picker, '7')
    expect(screen.getByTestId('record-heading')).toHaveTextContent('Person7 Test')
    expect(screen.getByText('(8 of 12)')).toBeInTheDocument()
  })

  it('describes every supported record type with its own section', () => {
    const names: [string, string][] = [
      ['prismhr-company', 'The employer'], ['isolved-direct-deposit', 'Where the money goes'], ['prismhr-deduction-recurring', 'The deduction'],
    ]
    for (const [name, section] of names) {
      const { unmount } = render(<RecordSummary records={repoSnapshot(name).records} />)
      expect(screen.getByRole('region', { name: section })).toBeInTheDocument()
      unmount()
    }
    const payroll = repoSnapshot('prismhr-payroll').records
    render(<RecordSummary records={payroll} />)
    expect(payroll.map((r) => r.record_type).sort()).toEqual(['paycheck', 'paycheck', 'payroll_run'])
    expect(screen.getByRole('region', { name: 'The pay run' })).toBeInTheDocument()
  })

  it('never renders a secret, even from a record that wrongly carries one', () => {
    render(<RecordSummary records={[record({ ssn: '123-45-6789', account_number: '000123456789', first_name: 'Ann', last_name: 'Lee' })]} />)
    const text = document.body.textContent ?? ''
    expect(text).toContain('Ann Lee')
    expect(text).not.toContain('123-45-6789')
    expect(text).not.toContain('000123456789')
  })

  it('renders nothing for no records', () => {
    const { container } = render(<RecordSummary records={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('StoryPanel', () => {
  it('counts converted, preserved and withheld values from the response', () => {
    const records = repoSnapshot('prismhr-employee').records
    render(<StoryPanel story={storyFor('prismhr', unit({ records }))} />)
    const withheld = records.reduce((n, r) => n + r.provenance.redacted_paths.length, 0)
    expect(withheld).toBeGreaterThan(0)
    expect(within(screen.getByTestId('story-withheld')).getByText(`${withheld} ${withheld === 1 ? 'value' : 'values'}`)).toBeInTheDocument()
    expect(screen.getByTestId('story-converted')).toHaveTextContent('across 2 records')
    expect(screen.getByTestId('story-preserved')).toHaveTextContent('nothing is dropped')
  })

  it('explains issues in plain English, merged by code with a count', () => {
    const story = storyFor('prismhr', unit({ issues: [issue('unmapped_code', 'info', '$.employee[1]'), issue('unmapped_code'), issue('runs_derived'), issue('earnings_total_mismatch', 'warning'), issue('missing_id', 'error')] }))
    render(<StoryPanel story={story} />)
    expect(within(screen.getByTestId('story-notUnderstood')).getByText(/documentation does not explain/)).toBeInTheDocument()
    expect(within(screen.getByTestId('story-notUnderstood')).getByText('(×2)')).toBeInTheDocument()
    expect(screen.getByTestId('story-providerGaps')).toHaveTextContent('does not send a pay-run record')
    expect(screen.getByTestId('story-notes')).toHaveTextContent('do not add up')
    expect(screen.getByTestId('story-problems')).toHaveTextContent('no ID')
  })

  it('falls back to the server’s own message for a code it does not know, and hides empty groups', () => {
    render(<StoryPanel story={storyFor('x', unit({ issues: [issue('brand_new_code', 'warning')] }))} />)
    expect(screen.getByTestId('story-notes')).toHaveTextContent('server says brand_new_code')
    for (const g of ['problems', 'notUnderstood', 'providerGaps']) expect(screen.queryByTestId(`story-${g}`)).not.toBeInTheDocument()
  })
})
