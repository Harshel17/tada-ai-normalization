import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { ConvertResponse, UnitResult } from '../../api/types'
import { record } from '../../test/utils'
import { ResultsOverview } from './ResultsOverview'

const unit = (records: UnitResult['records']): UnitResult => ({ file: 'f.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: {}, issues: [], records, traces: [] })
const conv = (records: UnitResult['records']): ConvertResponse => ({ provider: 'prismhr', files: [], suggestions: [], results: [unit(records)] })

describe('ResultsOverview', () => {
  it('pluralizes record types correctly, including company/companies', () => {
    const one = [record({ record_type: 'company', source_id: 'C', extras: {} }), record({ source_id: 'E', extras: {} })]
    render(<ResultsOverview conversion={conv(one)} />)
    expect(screen.getByText('1 company, 1 employee')).toBeInTheDocument()
  })
  it('uses the plural for several and never produces "companie"', () => {
    const many = [1, 2].flatMap((i) => [record({ record_type: 'company', source_id: `C${i}`, extras: {} }), record({ record_type: 'payroll_run', source_id: `R${i}`, extras: {} }), record({ record_type: 'direct_deposit', source_id: `D${i}`, extras: {} })])
    render(<ResultsOverview conversion={conv(many)} />)
    expect(screen.getByText('2 companies, 2 payroll runs, 2 direct deposits')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/companie\b/)
  })
  it('lists readable relationships and reveals exact ids only on request', async () => {
    const e = record({ source_id: 'E9', first_name: 'Ann', last_name: 'Lee', extras: {} })
    const p = record({ record_type: 'paycheck', source_id: 'V-1', run_id: 'B-1', employee_id: 'E9', extras: {} })
    render(<ResultsOverview conversion={conv([e, p])} />)
    await userEvent.click(screen.getByRole('button', { name: /how these records connect/ }))
    const list = screen.getByRole('list', { name: 'Record relationships' })
    expect(list).toHaveTextContent('Paycheck V-1')
    expect(list).toHaveTextContent('Employee Ann Lee')
    expect(list).toHaveTextContent('not in this data') // the run was not converted
    expect(screen.queryAllByTestId('exact-ids')).toHaveLength(0)
    await userEvent.click(screen.getByRole('checkbox', { name: /exact record ids/ }))
    expect(screen.getAllByTestId('exact-ids')[0]).toHaveTextContent('V-1 → B-1')
  })
})
