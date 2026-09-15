// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { EvidenceDrawer } from './App'

afterEach(cleanup)
it('does not expose an invisible focusable drawer', () => {
  render(<EvidenceDrawer detail={null} loading={false} onClose={() => {}} onFact={() => {}} />)
  expect(screen.queryByRole('dialog')).toBeNull()
})
it('focuses the dialog, handles Escape and restores focus', () => {
  const opener = document.createElement('button')
  document.body.append(opener); opener.focus()
  const close = vi.fn()
  const { unmount } = render(<EvidenceDrawer detail={null} loading={true} onClose={close} onFact={() => {}} />)
  expect(document.activeElement).toBe(screen.getByRole('dialog'))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(close).toHaveBeenCalledOnce()
  unmount(); expect(document.activeElement).toBe(opener); opener.remove()
})
