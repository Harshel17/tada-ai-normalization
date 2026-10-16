import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { mockFetch, renderWithClient } from './test/utils'

vi.mock('./components/JsonEditor', () => ({ JsonEditor: () => <div /> }))
afterEach(() => { vi.unstubAllGlobals(); window.location.hash = '' })

const routes = [
  { match: /health/, reply: { body: { status: 'ok', database: 'up' } } },
  { match: /stats/, reply: { body: { providers: ['prismhr', 'isolved', 'ukg-ready'], entity_types: [], scopes: [] } } },
  { match: /__fixtures/, reply: { body: { fixtures: [] } } },
]

describe('App shell', () => {
  it('lands on Normalize with no Overview in the main navigation', () => {
    mockFetch(routes)
    renderWithClient(<App />)
    expect(screen.getByRole('heading', { name: 'Three payroll providers. One consistent data model.' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Sections' })
    expect(Array.from(nav.querySelectorAll('a')).map((a) => a.textContent)).toEqual(['Normalize', 'Pipeline Explorer', 'Data Explorer', 'Test Runs'])
    expect(screen.queryByRole('heading', { name: 'Overview' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Normalize' })).toHaveAttribute('aria-current', 'page')
  })

  it('keeps the other sections reachable, and the status page by link and URL', async () => {
    mockFetch(routes)
    renderWithClient(<App />)
    await userEvent.setup().click(screen.getByRole('link', { name: 'Test Runs' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Test Runs' })).toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Test Runs' })).toHaveAttribute('aria-current', 'page')
    await userEvent.setup().click(screen.getByRole('link', { name: 'System status' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Overview' })).toBeInTheDocument())
  })

  it('opens the section named in the URL, and Advanced mode is available but quiet', () => {
    window.location.hash = '#/normalize'
    mockFetch(routes)
    renderWithClient(<App />)
    expect(screen.getByRole('radio', { name: 'Normalize' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Advanced mode' })).not.toBeChecked()
  })

  it('treats an unknown route as the Normalize screen', () => {
    window.location.hash = '#/nonsense'
    mockFetch(routes)
    renderWithClient(<App />)
    expect(screen.getByRole('heading', { name: 'Three payroll providers. One consistent data model.' })).toBeInTheDocument()
  })

  it('says it is a local tool for synthetic data', () => {
    mockFetch(routes)
    renderWithClient(<App />)
    expect(screen.getByText(/Local tool for synthetic data/)).toBeInTheDocument()
  })
})
