import { expect, test, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { readFileSync } from 'node:fs'

const fixture = (name: string) => fileURLToPath(new URL(`../../tests/fixtures/photos/${name}`, import.meta.url))

async function tapPoint(page: Page, boardIndex: number, size: number, x: number, y: number) {
  const board = page.locator('svg.board').nth(boardIndex)
  await board.scrollIntoViewIfNeeded()
  const box = (await board.boundingBox())!
  const span = size - 1 + 1.9
  await page.mouse.click(box.x + ((0.95 + x) / span) * box.width, box.y + ((0.95 + y) / span) * box.height)
}

async function noHorizontalOverflow(page: Page) {
  const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])
  expect(sw).toBeLessThanOrEqual(iw)
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.clear())
  await page.goto('/')
  await expect(page.getByTestId('engine-chip')).toHaveText(/KataGo ready/, { timeout: 120_000 })
})

test('manual position: moves, real KataGo analysis, PV stepping, layout', async ({ page }) => {
  await page.getByRole('radio', { name: /Quick/ }).click()
  await tapPoint(page, 0, 19, 15, 3) // B Q16
  await tapPoint(page, 0, 19, 3, 15) // W D4
  await expect(page.getByTestId('candidates')).toBeVisible()
  await expect(page.getByTestId('overview')).toContainText("Black's win probability")
  await expect(page.getByTestId('overview')).toContainText('Black to play')
  await expect(page.getByTestId('score-lead')).toHaveText(/Black \+|White \+|Even/)
  const visits = Number((await page.getByTestId('visits').textContent())!.replace(/,/g, ''))
  expect(visits).toBeGreaterThan(0)
  await expect(page.getByTestId('engine-info')).toContainText('KataGo')
  await page.getByRole('button', { name: 'Line' }).first().click()
  await page.getByTestId('pv-next').click()
  await expect(page.getByTestId('pv')).toContainText('move 2')
  await noHorizontalOverflow(page)
  // occupied point is rejected with a message
  await page.getByRole('button', { name: 'Close' }).click()
  await tapPoint(page, 0, 19, 15, 3)
  await expect(page.getByTestId('toast')).toContainText('occupied')
})

test('SGF export and import round trip', async ({ page }) => {
  await page.getByRole('checkbox', { name: 'Auto' }).uncheck()
  await tapPoint(page, 0, 19, 16, 3)
  await tapPoint(page, 0, 19, 2, 15)
  await page.getByTestId('pass').click()
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Save SGF' }).click()])
  const path = await dl.path()
  const sgf = readFileSync(path!, 'utf8')
  expect(sgf).toContain(';B[qd]')
  expect(sgf).toContain(';W[cp]')
  expect(sgf).toContain(';B[]')
  await page.getByRole('button', { name: 'Undo' }).click()
  await page.locator('input[type=file][accept*=".sgf"]').setInputFiles({ name: 'g.sgf', mimeType: 'application/x-go-sgf', buffer: Buffer.from(sgf) })
  await expect(page.getByText('Move 3')).toBeVisible()
})

test('photo: upload, review, correct, choose player, confirm, analyse', async ({ page }) => {
  await page.getByTestId('tab-photo').click()
  await page.getByTestId('photo-size-13').click()
  await page.getByTestId('choose-photo-input').setInputFiles(fixture('tengen_angled_easy_1.jpeg'))
  await expect(page.getByTestId('confirm-position')).toBeVisible({ timeout: 90_000 })
  await expect(page.getByTestId('stone-counts')).toContainText('5')
  // correction + undo
  const before = await page.getByTestId('stone-counts').textContent()
  await tapPoint(page, 0, 13, 0, 0)
  await expect(page.getByTestId('stone-counts')).not.toHaveText(before!)
  await page.getByTestId('undo-edit').click()
  await expect(page.getByTestId('stone-counts')).toHaveText(before!)
  // analysis is impossible until the next player is chosen
  await expect(page.getByTestId('confirm-position')).toBeDisabled()
  await page.getByTestId('photo-player-W').click()
  await page.getByTestId('confirm-position').click()
  await expect(page.getByTestId('candidates')).toBeVisible()
  await expect(page.getByTestId('overview')).toContainText('White to play')
  await expect(page.getByText('Position from a photo')).toBeVisible()
  await noHorizontalOverflow(page)
})

test('photo: unreadable file shows a clear error', async ({ page }) => {
  await page.getByTestId('tab-photo').click()
  await page.getByTestId('choose-photo-input').setInputFiles({ name: 'x.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('not an image') })
  await expect(page.getByTestId('photo-error')).toContainText('not a supported image')
})

test('photo: wrong board size asks for corners and suggests the right size', async ({ page }) => {
  await page.getByTestId('tab-photo').click()
  await page.getByTestId('photo-size-19').click()
  await page.getByTestId('choose-photo-input').setInputFiles(fixture('tengen_angled_easy_1.jpeg'))
  await expect(page.getByTestId('use-corners')).toBeVisible({ timeout: 90_000 })
  await expect(page.getByText('Try 13×13')).toBeVisible()
  await noHorizontalOverflow(page)
})
