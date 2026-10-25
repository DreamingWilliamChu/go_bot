import { expect, test, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'

// Bonus-turn Go. Determinism without a seed API: with an allocation equal to the horizon H every one
// of that player's first H turns is a bonus turn (probability 1), and an allocation of 0 never triggers.

const fixture = (name: string) => fileURLToPath(new URL(`../../tests/fixtures/photos/${name}`, import.meta.url))

async function tapPoint(page: Page, size: number, x: number, y: number) {
  const board = page.locator('svg.board').first()
  await board.scrollIntoViewIfNeeded()
  const box = (await board.boundingBox())!
  const span = size - 1 + 1.9
  await page.mouse.click(box.x + ((0.95 + x) / span) * box.width, box.y + ((0.95 + y) / span) * box.height)
}

async function noHorizontalOverflow(page: Page) {
  const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])
  expect(sw).toBeLessThanOrEqual(iw)
}

async function newBonusGame(
  page: Page,
  opts: { size?: number; black: number; white: number; horizon?: number; trigger?: 'random' | 'judge' },
) {
  await page.getByTestId('bonus-mode').click()
  await expect(page.getByTestId('bonus-entry-dialog')).toBeVisible()
  await page.getByTestId(`entry-trigger-${opts.trigger ?? 'random'}`).click()
  if (opts.size) await page.getByTestId(`entry-size-${opts.size}`).click()
  if (opts.horizon) await page.getByTestId('entry-horizon').fill(String(opts.horizon))
  await page.getByTestId('entry-black').fill(String(opts.black))
  await page.getByTestId('entry-white').fill(String(opts.white))
  await page.getByTestId('bonus-new').click()
  await expect(page.getByTestId('bonus-status')).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.clear())
  await page.goto('/')
  await expect(page.getByTestId('engine-chip')).toHaveText(/KataGo/, { timeout: 120_000 })
  const auto = page.getByRole('checkbox', { name: 'Auto' })
  if (await auto.isChecked()) await auto.uncheck()
})

test('bonus turn: announced, three moves with a bounded lock, then the opponent', async ({ page }) => {
  await newBonusGame(page, { size: 9, black: 2, white: 0, horizon: 2 })
  await expect(page.getByTestId('bonus-announcement')).toHaveText('Bonus! Black plays 3 moves in a row this turn.')
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 1 · White: 0')
  const keep = page.getByTestId('keep-color')
  await expect(keep).toHaveText('Keep Black · bonus 1/3')
  await expect(keep).toHaveAttribute('aria-pressed', 'true')
  await tapPoint(page, 9, 2, 2)
  await expect(page.getByTestId('bonus-turn')).toContainText('Black bonus turn — 2 moves remaining')
  await expect(keep).toHaveText('Keep Black · bonus 2/3')
  // a manual tap cannot extend or grant anything
  await keep.click()
  await expect(page.getByTestId('toast')).toContainText('Taps cannot change it')
  await expect(page.getByTestId('bonus-turn')).toContainText('2 moves remaining')
  await tapPoint(page, 9, 3, 2)
  await tapPoint(page, 9, 4, 2)
  // three moves in total, then the lock releases and White plays a normal turn
  await expect(page.getByTestId('bonus-turn')).toContainText('White to play — normal turn (1 move)')
  await expect(keep).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByTestId('to-play')).toContainText('White to play')
  const board = page.locator('svg.board').first()
  await expect(board.locator('circle.stone-b')).toHaveCount(3)
  await tapPoint(page, 9, 6, 6)
  // Black's second (forced) bonus is announced before Black moves
  await expect(page.getByTestId('bonus-announcement')).toBeVisible()
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 0 · White: 0')
  // undo goes back into White's turn and restores the counts; redo brings the same bonus back
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 1 · White: 0')
  await page.getByRole('button', { name: 'Redo' }).click()
  await expect(page.getByTestId('bonus-announcement')).toBeVisible()
  await noHorizontalOverflow(page)
})

test('a pass ends a bonus turn; two pass-only turns end the game', async ({ page }) => {
  await newBonusGame(page, { size: 9, black: 1, white: 0, horizon: 1 })
  await tapPoint(page, 9, 4, 4)
  await page.getByTestId('pass').click() // forfeits the third move
  await expect(page.getByTestId('bonus-turn')).toContainText('White to play')
  await page.getByTestId('pass').click() // White: pass-only
  await page.getByTestId('pass').click() // Black: pass-only -> game over
  await expect(page.getByTestId('bonus-result')).toContainText('Black wins by 73.5, scored as the board stands')
  await expect(page.getByTestId('bonus-result')).toContainText('Bonus schedule (now revealed): Black turns 1')
})

test('Bonus Mode never changes the standard game; manual Keep returns when it is off', async ({ page }) => {
  await tapPoint(page, 19, 3, 3) // a standard move
  await newBonusGame(page, { black: 1, white: 1, horizon: 30 })
  await expect(page.getByTestId('keep-color')).toHaveText(/Keep/)
  page.once('dialog', (d) => d.accept())
  await page.getByTestId('bonus-mode').click()
  await expect(page.getByText('Move 1')).toBeVisible() // the standard game is back as it was
  await expect(page.getByTestId('keep-color')).toHaveText('Keep White')
  // resume the same bonus game
  await page.getByTestId('bonus-mode').click()
  await page.getByTestId('bonus-resume').click()
  await expect(page.getByTestId('bonus-status')).toBeVisible()
})

test('analysis branch and photo import require an explicit bonus state', async ({ page }) => {
  await tapPoint(page, 19, 3, 3)
  await page.getByTestId('bonus-mode').click()
  await page.getByTestId('entry-trigger-random').click()
  await page.getByTestId('bonus-branch').click()
  const dialog = page.getByTestId('bonus-state-dialog')
  await expect(dialog).toContainText('History unknown')
  await page.getByTestId('bsd-remaining-B').fill('5')
  await page.getByTestId('bsd-turns-B').fill('88')
  await expect(dialog).toContainText('only 2 eligible turns remain')
  await expect(page.getByTestId('bsd-start')).toBeDisabled()
  await page.getByTestId('bsd-remaining-B').fill('1')
  await page.getByTestId('bsd-bonus-active').click()
  await page.getByTestId('bsd-start').click()
  await expect(page.getByTestId('bonus-provenance')).toContainText('History unknown')
  await expect(page.getByTestId('bonus-turn')).toContainText('White bonus turn — 3 moves remaining')

  // with Bonus Mode on, a confirmed photo goes through the same explicit dialog
  await page.getByTestId('tab-photo').click()
  await page.getByTestId('photo-size-13').click()
  await page.getByTestId('choose-photo-input').setInputFiles(fixture('tengen_angled_easy_1.jpeg'))
  await expect(page.getByTestId('confirm-position')).toBeVisible({ timeout: 90_000 })
  await page.getByTestId('photo-player-B').click()
  await page.getByTestId('confirm-position').click()
  await expect(page.getByTestId('bonus-state-dialog')).toContainText('cannot show the bonus history')
  await page.getByTestId('bsd-start').click()
  await expect(page.getByTestId('bonus-provenance')).toContainText('Started from a photo')
  await noHorizontalOverflow(page)
})

test('bonus analysis: source picker, bonus search labels, KataGo ignores future bonuses', async ({ page }) => {
  await newBonusGame(page, { size: 9, black: 2, white: 0, horizon: 2 })
  await page.getByRole('radio', { name: /Quick/ }).click()
  // the bonus search with the heuristic evaluator (needs no KataGo)
  await page.getByTestId('source-heuristic').click()
  await page.getByTestId('analyze').click()
  await expect(page.getByTestId('analysis-source')).toHaveText('Bonus search', { timeout: 60_000 })
  await expect(page.getByTestId('analysis-evaluator')).toHaveText('Heuristic evaluator')
  await expect(page.getByTestId('overview')).toContainText("Black's win estimate (approximate)")
  await expect(page.getByTestId('candidates')).toBeVisible()
  // a line keeps Black for the rest of the bonus turn
  await page.getByRole('button', { name: 'Line' }).first().click()
  await expect(page.locator('.pvm.B').first()).toBeVisible()
  await page.getByRole('button', { name: 'Close' }).click()
  // standard KataGo, labelled as ignoring future bonuses
  await page.getByTestId('source-katago').click()
  await page.getByTestId('analyze').click()
  await expect(page.getByTestId('analysis-source')).toHaveText('Standard KataGo', { timeout: 60_000 })
  await expect(page.getByTestId('analysis-footnote')).toContainText('ignores future bonuses')
  await noHorizontalOverflow(page)
})

test('Engine tab lists bonus-variant networks with their provenance', async ({ page }) => {
  await page.getByTestId('tab-settings').click()
  await expect(page.getByTestId('bonus-networks')).toContainText('Bonus-variant networks')
})

test('judge: Keep color grants a bonus before the first move; analysis never suggests one', async ({ page }) => {
  await newBonusGame(page, { size: 9, black: 2, white: 1, trigger: 'judge' })
  const keep = page.getByTestId('keep-color')
  // nothing happens by itself: the turn starts normal and the judge may grant a bonus
  await expect(page.getByTestId('bonus-turn')).toContainText('Black to play — normal turn (1 move)')
  await expect(keep).toHaveText('Grant bonus')
  // to the players and the analysis the judge's timing is random: a modelled chance is shown
  await expect(page.getByTestId('bonus-chance')).toContainText('The judge decides; modelled next-turn chance — Black 2/19')
  // and the bonus search never lists a bonus among its moves
  await page.getByRole('radio', { name: /Quick/ }).click()
  await page.getByTestId('source-heuristic').click()
  await page.getByTestId('analyze').click()
  await expect(page.getByTestId('analysis-source')).toHaveText('Bonus search', { timeout: 60_000 })
  await expect(page.getByTestId('candidates')).not.toContainText(/bonus/i)
  await expect(page.getByTestId('analysis-footnote')).toContainText('outside judge grants the bonuses')
  await keep.click()
  await expect(page.getByTestId('bonus-announcement')).toHaveText('Bonus! Black plays 3 moves in a row this turn.')
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 1 · White: 1')
  await expect(keep).toHaveText('Keep Black · bonus 1/3')
  await tapPoint(page, 9, 2, 2)
  // mid-turn the control cannot add or extend anything
  await keep.click()
  await expect(page.getByTestId('toast')).toContainText('Taps cannot change it')
  await expect(page.getByTestId('bonus-turn')).toContainText('Black bonus turn — 2 moves remaining')
  await tapPoint(page, 9, 3, 2)
  await tapPoint(page, 9, 4, 2)
  await expect(page.getByTestId('bonus-turn')).toContainText('White to play — normal turn (1 move)')
  await expect(keep).toHaveText('Grant bonus')
  await tapPoint(page, 9, 6, 6) // no bonus for White this time
  await expect(keep).toHaveText('Grant bonus')
  // the grant is an ordinary entry in the history: undo and redo step through it like moves
  const undo = page.getByRole('button', { name: 'Undo' })
  const redo = page.getByRole('button', { name: 'Redo' })
  const stones = page.locator('svg.board').first().locator('circle.stone-b, circle.stone-w')
  for (let i = 0; i < 4; i++) await undo.click() // White's move, then Black's three bonus moves
  await expect(stones).toHaveCount(0)
  await expect(page.getByTestId('bonus-announcement')).toHaveText('Bonus! Black plays 3 moves in a row this turn.')
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 1 · White: 1')
  await undo.click() // the grant itself
  await expect(page.getByTestId('bonus-turn')).toContainText('Black to play — normal turn (1 move)')
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 2 · White: 1')
  await expect(keep).toHaveText('Grant bonus')
  await redo.click() // the grant comes back...
  await expect(page.getByTestId('bonus-announcement')).toHaveText('Bonus! Black plays 3 moves in a row this turn.')
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 1 · White: 1')
  for (let i = 0; i < 4; i++) await redo.click() // ...and so do the moves after it
  await expect(stones).toHaveCount(4)
  await expect(page.getByTestId('bonus-turn')).toContainText('Black to play — normal turn (1 move)')
  await expect(redo).toBeDisabled()
  // as with ordinary moves, a new entry after undo clears the redo list
  await undo.click()
  await expect(redo).toBeEnabled()
  await keep.click() // the judge grants White's bonus instead of the move that was undone
  await expect(redo).toBeDisabled()
  await expect(page.getByTestId('bonuses-left')).toHaveText('Bonuses left — Black: 1 · White: 0')
  await noHorizontalOverflow(page)
})
