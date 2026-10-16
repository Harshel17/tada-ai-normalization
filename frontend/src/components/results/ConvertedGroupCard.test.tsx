import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { FileDetection, UnitResult } from '../../api/types'
import { record } from '../../test/utils'
import { ConvertedGroupCard } from './ConvertedGroupCard'

const unit = (over: Partial<UnitResult>): UnitResult => ({ file: 'deductions.json', entity: 'deduction', label: 'x', status: 'converted', missing: [], counts: { deduction: 2 }, issues: [], records: [record({ record_type: 'deduction', source_id: 'A', name: 'Medical' }), record({ record_type: 'deduction', source_id: 'B', deduction_code: 'RET' })], traces: [], ...over })
const needsOwner: FileDetection[] = [{ name: 'deductions.json', detection: { status: 'ok', units: [{ entity: 'deduction', shape: 'x', label: 'x', keys: null, counts: {}, derived_counts: {}, evidence: [], requires: ['company_id', 'employee_id'], optional: [], nested_in: null, convention: false, strength: 'strong', notes: [] }], candidates: [], warnings: [], message: null, provider_hint: null } }]
const mount = (u: UnitResult, extra: Partial<React.ComponentProps<typeof ConvertedGroupCard>> = {}) =>
  render(<ul><ConvertedGroupCard unit={u} provider="prismhr" all={[u]} detections={needsOwner} defaultOpen={false} assigned={null} {...extra} /></ul>)

describe('ConvertedGroupCard', () => {
  it('is informative before it is opened: title, source, status, statistics and a preview', () => {
    mount(unit({}))
    const card = screen.getByTestId('unit-deductions.json-deduction')
    expect(card).toHaveTextContent('Paycheck deductions')
    expect(card).toHaveTextContent('Source: deductions.json')
    expect(card).toHaveTextContent('converted')
    expect(within(card).getByLabelText('Summary')).toHaveTextContent('2')
    expect(screen.getByTestId('unit-preview')).toHaveTextContent('Medical · RET')
    expect(screen.getByTestId('found-deductions.json-deduction')).not.toHaveAttribute('open')
    expect(card).toHaveTextContent('2 deductions') // the plain count stays available to assistive technology
  })
  it('an assigned owner is described as your confirmation, never as verified', () => {
    mount(unit({}), { assigned: { id: 'E1001', basis: 'confirmed' } })
    const note = screen.getByTestId('unit-owner')
    expect(note).toHaveTextContent('Owner assigned through your confirmation (employee E1001); not independently verified from source data.')
    expect(note.textContent).not.toMatch(/verified owner|is verified|confirmed correct/i)
  })
  it('the example set convention is labelled as a convention', () => {
    mount(unit({}), { assigned: { id: 'E1001', basis: 'documented' } })
    expect(screen.getByTestId('unit-owner')).toHaveTextContent(/example set’s convention, which the file does not state; not independently verified/)
  })
  it('an owner named in the file is said to be named in the file', () => {
    mount(unit({}), { detections: [{ ...needsOwner[0]!, detection: { ...needsOwner[0]!.detection, units: [{ ...needsOwner[0]!.detection.units[0]!, requires: ['company_id'] }] } }] })
    expect(screen.getByTestId('unit-owner')).toHaveTextContent('named in the source file itself')
  })
  it('keeps the detailed view behind one control and opens it on request', async () => {
    mount(unit({}))
    expect(screen.getByText('View full details')).toBeInTheDocument()
    await userEvent.click(screen.getByText(/What we found/))
    expect(screen.getByTestId('found-deductions.json-deduction')).toHaveAttribute('open')
    expect(screen.getByTestId('readable-summary')).toBeInTheDocument()
  })
  it('a section waiting for an identifier shows what is missing and no invented statistics', () => {
    mount(unit({ status: 'needs_context', missing: ['employee_id'], counts: {}, records: [] }))
    const card = screen.getByTestId('unit-deductions.json-deduction')
    expect(card).toHaveTextContent('Needs: Employee ID. Nothing was guessed.')
    expect(within(card).queryByLabelText('Summary')).not.toBeInTheDocument()
    expect(screen.queryByTestId('found-deductions.json-deduction')).not.toBeInTheDocument()
  })
  it('shows no account digits anywhere for deposits', () => {
    const dep = unit({ file: 'd.json', entity: 'direct_deposit', counts: { direct_deposit: 1 }, records: [record({ record_type: 'direct_deposit', source_id: '111000025-6789-C-F-', account_number_masked: '****6789', bank_name: 'Bank', account_type: 'checking', allocation_type: 'fixed_amount', active: true })] })
    render(<ul><ConvertedGroupCard unit={dep} provider="prismhr" all={[dep]} detections={[]} defaultOpen={false} assigned={null} /></ul>)
    const collapsed = screen.getByTestId('unit-d.json-direct_deposit').querySelector('dl')!.textContent + screen.getAllByTestId('unit-preview').map((p) => p.textContent).join(' ')
    expect(collapsed).not.toMatch(/6789|111000025/)
  })
})
