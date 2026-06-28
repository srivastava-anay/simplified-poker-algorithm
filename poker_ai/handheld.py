"""320x240 Raspberry Pi handheld poker game."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from textwrap import wrap

from .cards import Card
from .table import MultiplayerTable, TableEvent

WIDTH = 320
HEIGHT = 240
ROTATION = 90
GUTTER = 6
LOG_W = 98
LOG_X = WIDTH - LOG_W - GUTTER
PLAY_X = GUTTER
PLAY_W = LOG_X - PLAY_X - GUTTER
MAX_BOTS = 6
TABLE_TOP = 30
TABLE_BOTTOM = 172
HERO_TOP = 176
DEBOUNCE_SECONDS = 0.035

BUTTON_PINS = {
    "J": 5,
    "K": 6,
    "L": 13,
}

BG = "#101719"
PANEL = "#172326"
FELT = "#1f6b4d"
FELT_DARK = "#124332"
INK = "#edf2e8"
MUTED = "#9fb0a7"
GOLD = "#f1c75b"
RED = "#db4d4d"
BLUE = "#4f9fd8"
GREEN = "#58b875"
DISABLED = "#364044"
CARD_FACE = "#f7f2df"
CARD_EDGE = "#d7c9a0"
BLACK_CARD = "#25292c"
# The ILI9341 path renders this with red/blue swapped, landing near #a90505.
RED_CARD = "#0505a9"
SUIT_SYMBOLS = {
    "s": "\u2660",
    "h": "\u2665",
    "d": "\u2666",
    "c": "\u2663",
}
NUMPY = None
NUMPY_IMPORT_ATTEMPTED = False


@dataclass(frozen=True)
class SoftButton:
    key: str
    label: str
    enabled: bool


class MissingHardwareDependencies(RuntimeError):
    """Raised when Raspberry Pi display/button libraries are unavailable."""


class HandheldPokerCore:
    """Shared game flow for the three-button handheld interface."""

    def _init_game_state(self) -> None:
        self.table: MultiplayerTable | None = None
        self.bot_count = 1
        self.mode = "setup"
        self.event_cursor = 0
        self.visible_events: list[TableEvent] = []
        self.log_round_key: tuple[int, str] | None = None
        self.bet_selection: dict[str, int] | None = None
        self.held_keys: dict[str, str | None] = {}
        self.hold_ticks: dict[str, int] = {}
        self.release_jobs: dict[str, str] = {}
        self.soft_buttons = [
            SoftButton("J", "- Bots", True),
            SoftButton("K", "Start", True),
            SoftButton("L", "+ Bots", True),
        ]
        self.busy = False
        self._refresh()

    def after(self, delay_ms: int, callback: Callable[[], None]) -> str:
        raise NotImplementedError

    def after_cancel(self, job: str) -> None:
        raise NotImplementedError

    def _present(self) -> None:
        pass

    def _clear(self) -> None:
        raise NotImplementedError

    def _rect(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        fill: str | None = None,
        outline: str | None = None,
        width: int = 1,
    ) -> None:
        raise NotImplementedError

    def _oval(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        fill: str | None = None,
        outline: str | None = None,
        width: int = 1,
    ) -> None:
        raise NotImplementedError

    def _text(
        self,
        x: int,
        y: int,
        *,
        text: object = "",
        fill: str | None = None,
        font: object = None,
        anchor: str | None = None,
        width: int | None = None,
    ) -> None:
        raise NotImplementedError

    def _start_hand(self) -> None:
        if self.table is None:
            simulations = max(120, 450 - self.bot_count * 40)
            self.table = MultiplayerTable(
                human_players=1,
                bot_players=self.bot_count,
                starting_stack=1000,
                small_blind=5,
                big_blind=10,
                simulations=simulations,
                defer_round_advance=True,
            )
            self.table.players[0].name = "You"
            self.mode = "game"
        self.event_cursor = 0
        self.visible_events = []
        self.log_round_key = None
        self.bet_selection = None
        self.busy = False
        self.table.start_hand()
        self._refresh()
        self._continue_turn()

    def _continue_turn(self) -> None:
        if self.table is None:
            return
        self._refresh()
        if self.table.awaiting_round_advance:
            self.busy = False
            self._refresh(status="Continue")
            return
        if self.table.hand_over:
            self.busy = False
            self._refresh()
            return
        actor = self.table.actor
        if actor is None:
            return
        if actor.is_bot:
            self.busy = True
            self._refresh(status=f"{actor.name} thinking")
            self.after(550, self._bot_turn)
        else:
            self.busy = False
            self._refresh()

    def _advance_after_pause(self) -> None:
        if self.table is None:
            return
        self.table.advance_round()
        self.busy = False
        self._continue_turn()

    def _bot_turn(self) -> None:
        if self.table is None:
            return
        try:
            self.table.bot_act()
        finally:
            self.busy = False
        self._continue_turn()

    def _key_down(self, key: str) -> None:
        if self.mode == "setup":
            self._press(key, held=False)
            return
        release_job = self.release_jobs.pop(key, None)
        if release_job is not None:
            self.after_cancel(release_job)
            return
        if key in self.held_keys:
            return
        self.held_keys[key] = None
        self.hold_ticks[key] = 0
        self._press(key, held=False)
        if self.bet_selection is not None and key in {"J", "L"}:
            self.held_keys[key] = self.after(300, lambda value=key: self._hold_tick(value))

    def _key_up(self, key: str) -> None:
        if self.mode == "setup":
            return
        old_job = self.release_jobs.pop(key, None)
        if old_job is not None:
            self.after_cancel(old_job)
        self.release_jobs[key] = self.after(80, lambda value=key: self._finish_key_up(value))

    def _finish_key_up(self, key: str) -> None:
        self.release_jobs.pop(key, None)
        job = self.held_keys.pop(key, None)
        if job is not None:
            self.after_cancel(job)
        self.hold_ticks.pop(key, None)

    def _hold_tick(self, key: str) -> None:
        if key not in self.held_keys:
            return
        self.hold_ticks[key] = self.hold_ticks.get(key, 0) + 1
        self._press(key, held=True)
        delay = self._repeat_delay(key)
        self.held_keys[key] = self.after(delay, lambda value=key: self._hold_tick(value))

    def _repeat_delay(self, key: str) -> int:
        ticks = self.hold_ticks.get(key, 0)
        if ticks < 10:
            return max(35, 95 - ticks * 6)
        if ticks < 35:
            return max(16, 45 - (ticks - 10))
        if ticks < 80:
            return max(8, 18 - (ticks - 35) // 5)
        return 6

    def _press(self, key: str, held: bool = False) -> None:
        if self.mode == "setup":
            if held:
                return
            if key == "J":
                self.bot_count = max(1, self.bot_count - 1)
                self._refresh()
            elif key == "L":
                self.bot_count = min(MAX_BOTS, self.bot_count + 1)
                self._refresh()
            elif key == "K":
                self._start_hand()
            return
        if self.busy:
            return
        if self.table is None:
            return
        if self.table.awaiting_round_advance:
            if key in {"J", "K", "L"} and not held:
                self._advance_after_pause()
            return
        if self.table.hand_over:
            if key == "K" and not held and not self.table.match_over:
                self._start_hand()
            return
        actor_index = self.table.actor_index
        if actor_index is None or self.table.players[actor_index].is_bot:
            return

        if self.bet_selection is not None:
            self._press_bet_selection(key, held)
            return

        legal = self.table.legal_actions(actor_index)
        to_call = int(legal.get("to_call", 0))
        try:
            if key == "J" and to_call:
                self.table.act(actor_index, "fold")
            elif key == "K":
                self.table.act(actor_index, "call" if to_call else "check")
            elif key == "L" and (legal.get("can_bet") or legal.get("can_raise")):
                if held:
                    return
                self._enter_bet_selection(legal)
                return
            else:
                return
        except ValueError as exc:
            self._flash(str(exc))
            return
        self._continue_turn()

    def _enter_bet_selection(self, legal: dict[str, int | bool]) -> None:
        assert self.table is not None
        actor = self.table.actor
        committed = actor.street_contribution if actor is not None else 0
        minimum = max(0, int(legal["minimum_total"]) - committed)
        maximum = max(minimum, int(legal["maximum_total"]) - committed)
        self.bet_selection = {
            "amount": minimum,
            "minimum": minimum,
            "maximum": maximum,
            "committed": committed,
            "held_minimum": 0,
        }
        self._refresh()

    def _press_bet_selection(self, key: str, held: bool) -> None:
        assert self.table is not None
        assert self.bet_selection is not None
        if key == "K":
            if not held:
                self._confirm_bet_selection()
            return
        if key == "J":
            if held:
                amount = self.bet_selection["amount"]
                minimum = self.bet_selection["minimum"]
                step = self._bet_step("J", held=True)
                self.bet_selection["amount"] = max(minimum, amount - step)
                if self.bet_selection["amount"] <= minimum:
                    self.bet_selection["held_minimum"] = 1
                self._refresh()
                return
            self._lower_bet_selection()
            return
        if key == "L":
            self._raise_bet_selection(held)

    def _lower_bet_selection(self) -> None:
        assert self.bet_selection is not None
        amount = self.bet_selection["amount"]
        minimum = self.bet_selection["minimum"]
        if amount <= minimum:
            self.bet_selection["held_minimum"] = 0
            self.bet_selection = None
            self._refresh()
            return
        step = self._bet_step("J", held=False)
        self.bet_selection["amount"] = max(minimum, amount - step)
        if self.bet_selection["amount"] <= minimum:
            self.bet_selection["held_minimum"] = 0
        self._refresh()

    def _raise_bet_selection(self, held: bool) -> None:
        assert self.bet_selection is not None
        amount = self.bet_selection["amount"]
        maximum = self.bet_selection["maximum"]
        step = self._bet_step("L", held)
        self.bet_selection["amount"] = min(maximum, amount + step)
        self.bet_selection["held_minimum"] = 0
        self._refresh()

    def _confirm_bet_selection(self) -> None:
        assert self.table is not None
        assert self.bet_selection is not None
        index = self.table.actor_index
        if index is None:
            return
        amount = self.bet_selection["amount"]
        maximum = self.bet_selection["maximum"]
        committed = self.bet_selection["committed"]
        target_total = committed + amount
        action = "allin" if amount >= maximum else ("raise" if self.table.current_bet else "bet")
        self.bet_selection = None
        try:
            self.table.act(index, action, target_total if action != "allin" else None)
        except ValueError as exc:
            self._flash(str(exc))
            return
        self._continue_turn()

    def _bet_step(self, key: str, held: bool) -> int:
        return 10 if held else 1

    def _flash(self, text: str) -> None:
        self._refresh(status=text[:28])
        self.after(900, self._refresh)

    def _refresh(self, status: str | None = None) -> None:
        if self.mode == "setup":
            self._set_setup_buttons()
            self._clear()
            self._draw_setup()
            self._present()
            return
        if self.table is None:
            return
        self._pull_events()
        self._set_soft_buttons()
        self._clear()
        self._draw_background()
        self._draw_header()
        self._draw_opponents()
        self._draw_board()
        self._draw_hero(status)
        self._draw_log()
        self._present()

    def _pull_events(self) -> None:
        assert self.table is not None
        round_key = (self.table.hand_number, self.table.stage.value)
        if round_key != self.log_round_key:
            self.log_round_key = round_key
            self.visible_events = []
            self.event_cursor = self._round_event_cursor()
        if self.event_cursor < len(self.table.events):
            self.visible_events.extend(self.table.events[self.event_cursor :])
            self.visible_events = self.visible_events[-40:]
            self.event_cursor = len(self.table.events)

    def _round_event_cursor(self) -> int:
        assert self.table is not None
        if self.table.stage.value == "preflop":
            return 0
        for index in range(len(self.table.events) - 1, -1, -1):
            event = self.table.events[index]
            if event.kind == "street" and event.text.lower().startswith(self.table.stage.value):
                return index + 1
        return len(self.table.events)

    def _set_soft_buttons(self) -> None:
        assert self.table is not None
        if self.bet_selection is not None:
            amount = self.bet_selection["amount"]
            at_min = amount <= self.bet_selection["minimum"]
            at_max = amount >= self.bet_selection["maximum"]
            self.soft_buttons = [
                SoftButton("J", "Back" if at_min else "- Amt", True),
                SoftButton("K", "All-in" if at_max else "Confirm", True),
                SoftButton("L", "Max" if at_max else "+ Amt", not at_max),
            ]
            return
        if self.table.awaiting_round_advance:
            self.soft_buttons = [
                SoftButton("J", "Continue", True),
                SoftButton("K", "Continue", True),
                SoftButton("L", "Continue", True),
            ]
            return
        if self.table.hand_over:
            self.soft_buttons = [
                SoftButton("J", "", False),
                SoftButton(
                    "K",
                    "Next" if not self.table.match_over else "Done",
                    not self.table.match_over,
                ),
                SoftButton("L", "", False),
            ]
            return
        index = self.table.actor_index
        if index is None or self.table.players[index].is_bot or self.busy:
            self.soft_buttons = [
                SoftButton("J", "", False),
                SoftButton("K", "...", False),
                SoftButton("L", "", False),
            ]
            return
        legal = self.table.legal_actions(index)
        to_call = int(legal["to_call"])
        self.soft_buttons = [
            SoftButton("J", "Fold", to_call > 0),
            SoftButton("K", f"Call {to_call}" if to_call else "Check", True),
            SoftButton(
                "L",
                "Raise" if legal["can_raise"] else "Bet",
                bool(legal["can_bet"]) or bool(legal["can_raise"]),
            ),
        ]

    def _set_setup_buttons(self) -> None:
        self.soft_buttons = [
            SoftButton("J", "- Bots", self.bot_count > 1),
            SoftButton("K", "Start", True),
            SoftButton("L", "+ Bots", self.bot_count < MAX_BOTS),
        ]

    def _draw_setup(self) -> None:
        self._rect(0, 0, WIDTH, HEIGHT, fill=BG, outline="")
        self._rect(
            10, 18, WIDTH - 10, HEIGHT - 10, fill=PANEL, outline="#2d3b40"
        )
        self._text(
            72,
            54,
            text="POKER",
            fill=GOLD,
            font=("Menlo", 20, "bold"),
        )
        self._text(
            72,
            84,
            text="How many bots?",
            fill=INK,
            font=("Menlo", 12, "bold"),
        )
        self._oval(190, 42, 276, 128, fill=FELT, outline=GOLD, width=2)
        self._text(
            233,
            73,
            text=str(self.bot_count),
            fill="white",
            font=("Menlo", 32, "bold"),
        )
        label = "bot" if self.bot_count == 1 else "bots"
        self._text(
            233,
            104,
            text=label,
            fill=MUTED,
            font=("Menlo", 11, "bold"),
        )
        total = self.bot_count + 1
        self._text(
            WIDTH // 2,
            158,
            text=f"{total} total players",
            fill=INK,
            font=("Menlo", 10),
        )
        self._text(
            WIDTH // 2,
            204,
            text="Left  Start  Right",
            fill=MUTED,
            font=("Menlo", 9, "bold"),
        )

    def _draw_background(self) -> None:
        assert self.table is not None
        self._rect(0, 0, WIDTH, HEIGHT, fill=BG, outline="")
        self._rect(
            PLAY_X, TABLE_TOP, PLAY_X + PLAY_W, TABLE_BOTTOM, fill=FELT, outline=FELT_DARK
        )
        self._rect(
            LOG_X, TABLE_TOP, WIDTH - GUTTER, HEIGHT - 4, fill=PANEL, outline="#263438"
        )

    def _draw_header(self) -> None:
        assert self.table is not None
        title = f"HAND {self.table.hand_number}"
        stage = self.table.stage.value.upper()
        self._text(
            10, 10, anchor="nw", text=title, fill=GOLD, font=("Menlo", 10, "bold")
        )
        self._text(
            WIDTH // 2,
            10,
            anchor="n",
            text=f"POT: {self.table.pot}",
            fill=INK,
            font=("Menlo", 9, "bold"),
        )
        self._text(
            LOG_X + LOG_W // 2,
            10,
            anchor="n",
            text=stage,
            fill=MUTED,
            font=("Menlo", 9, "bold"),
        )

    def _draw_opponents(self) -> None:
        assert self.table is not None
        opponents = self.table.players[1:]
        slots = self._opponent_slots(len(opponents))
        compact = len(opponents) >= 5
        for index, (x, y, w, h) in enumerate(slots):
            self._draw_opponent(
                bot_index=index + 1,
                x=x,
                y=y,
                width=w,
                height=h,
                compact=compact,
            )

    @staticmethod
    def _opponent_slots(count: int) -> list[tuple[int, int, int, int]]:
        if count <= 0:
            return []
        if count <= 3:
            rows = [(count, 42)]
            width = 62 if count > 1 else 78
        elif count == 4:
            rows = [(2, 40), (2, 86)]
            width = 76
        elif count == 5:
            rows = [(3, 40), (2, 86)]
            width = 58
        else:
            rows = [(3, 40), (3, 86)]
            width = 58
        height = 38
        gap = 6
        slots: list[tuple[int, int, int, int]] = []
        for row_count, y in rows:
            total = row_count * width + (row_count - 1) * gap
            start_x = PLAY_X + (PLAY_W - total) // 2
            slots.extend((start_x + i * (width + gap), y, width, height) for i in range(row_count))
        return slots

    def _draw_opponent(
        self,
        bot_index: int,
        x: int,
        y: int,
        width: int,
        height: int,
        compact: bool,
    ) -> None:
        assert self.table is not None
        bot = self.table.players[bot_index]
        detail = f"{bot.stack}"
        if bot.street_contribution:
            detail += f"/{bot.street_contribution}"
        if bot.folded:
            self._draw_folded_opponent(bot_index, x, y, width, height, compact)
            return
        active = self.table.actor_index == bot_index
        fill = "#29423c" if active else "#18352d"
        outline = GOLD if active else "#3d8264"
        self._rect(
            x, y, x + width, y + height, fill=fill, outline=outline
        )
        self._text(
            x + 4,
            y + 4,
            anchor="nw",
            text=f"B{bot_index}",
            fill=GOLD,
            font=("Menlo", 8, "bold"),
        )
        if self.table.hand_over and bot.in_hand and bot.hole_cards:
            self._draw_showdown_cards(x, y, width, height, bot.hole_cards, compact)
        else:
            self._text(
                x + 4,
                y + height - 4,
                anchor="sw",
                text=detail,
                fill=INK,
                font=("Numbers", 8, "bold"),
            )

    def _draw_showdown_cards(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        cards: tuple[Card, Card],
        compact: bool,
    ) -> None:
        scale = 0.54 if compact else 0.64
        card_w = int(28 * scale)
        card_h = int(34 * scale)
        gap = 2
        total_w = card_w * 2 + gap
        start_x = x + width - total_w - 3
        card_y = min(y + height - card_h - 2, y + 11)
        self._draw_card(start_x, card_y, cards[0], scale=scale)
        self._draw_card(start_x + card_w + gap, card_y, cards[1], scale=scale)

    def _draw_folded_opponent(
        self,
        bot_index: int,
        x: int,
        y: int,
        width: int,
        height: int,
        compact: bool,
    ) -> None:
        w = min(width, 30 if compact else 36)
        h = min(height, 16 if compact else 18)
        x0 = x + (width - w) // 2
        y0 = y + (height - h) // 2
        self._rect(
            x0, y0, x0 + w, y0 + h, fill="#253034", outline="#526065"
        )
        self._text(
            x0 + w // 2,
            y0 + h // 2,
            text=f"B{bot_index} F",
            fill="#a7b3b5",
            font=("Menlo", 8, "bold"),
        )

    def _draw_board(self) -> None:
        assert self.table is not None
        board = self.table.board
        scale = 0.92
        card_w = int(28 * scale)
        gap = 12
        total_w = 5 * card_w + 4 * gap
        start_x = PLAY_X + (PLAY_W - total_w) // 2
        y = 132
        for index in range(5):
            x = start_x + index * (card_w + gap)
            if index < len(board):
                self._draw_card(x, y, board[index], scale=scale)
            else:
                self._draw_empty_card(x, y, scale=scale)

    def _draw_hero(self, status: str | None) -> None:
        assert self.table is not None
        hero = self.table.players[0]
        self._rect(
            PLAY_X, HERO_TOP, PLAY_X + PLAY_W, HEIGHT - 4, fill=PANEL, outline="#263438"
        )
        self._text(
            13, HERO_TOP + 14, anchor="nw", text="YOU", fill=GOLD, font=("Menlo", 8, "bold")
        )
        chips = f"{hero.stack}"
        if hero.street_contribution:
            chips += f"/{hero.street_contribution}"
        self._text(
            13, HERO_TOP + 33, anchor="nw", text=chips, fill=INK, font=("Numbers", 8, "bold")
        )

        if hero.hole_cards:
            self._draw_card(58, HERO_TOP + 16, hero.hole_cards[0], scale=0.92)
            self._draw_card(91, HERO_TOP + 16, hero.hole_cards[1], scale=0.92)
        message = status or self._status_text()
        self._text(
            128,
            HERO_TOP + 18,
            anchor="nw",
            text=message,
            fill=INK,
            font=("Menlo", 8 if len(message) < 22 else 7, "bold"),
            width=80,
        )

    def _draw_log(self) -> None:
        assert self.table is not None
        line_height = 9
        top_y = 34
        max_y = HEIGHT - 8
        max_lines = max(1, (max_y - top_y) // line_height)
        blocks: list[tuple[list[str], str]] = []
        for event in self.visible_events:
            if event.kind == "street":
                continue
            text = self._format_log_text(event.text)
            fill = GOLD if event.kind == "result" else "#d7e0da"
            wrapped = wrap(text, width=16, break_long_words=True) or [""]
            blocks.append((wrapped, fill))

        visible_blocks = self._fit_log_blocks(blocks, max_lines)
        used_lines = sum(len(lines) for lines, _fill in visible_blocks)
        y = max(top_y, max_y - used_lines * line_height)
        for lines, fill in visible_blocks:
            for text in lines:
                self._text(
                    LOG_X + 6,
                    y,
                    anchor="nw",
                    text=text,
                    fill=fill,
                    font=("Menlo", 8),
                    width=LOG_W - 12,
                )
                y += line_height

    @staticmethod
    def _fit_log_blocks(
        blocks: list[tuple[list[str], str]], max_lines: int
    ) -> list[tuple[list[str], str]]:
        visible: list[tuple[list[str], str]] = []
        used = 0
        for lines, fill in reversed(blocks):
            if used + len(lines) <= max_lines:
                visible.append((lines, fill))
                used += len(lines)
                continue
            remaining = max_lines - used
            if remaining > 0:
                visible.append((lines[-remaining:], fill))
            break
        return list(reversed(visible))

    @staticmethod
    def _format_log_text(text: str) -> str:
        if text.startswith("Hand "):
            _hand, separator, rest = text.partition(". ")
            if separator:
                text = rest
        replacements = (
            ("Player 1 has the button", "You have the button"),
            ("Player 1 posts", "You post"),
            ("Player 1 folds", "You fold"),
            ("Player 1 checks", "You check"),
            ("Player 1 calls", "You call"),
            ("Player 1 bets", "You bet"),
            ("Player 1 raises", "You raise"),
            ("Player 1 shows", "You show"),
            ("Player 1 wins", "You win"),
            ("Player 1 receives", "You receive"),
        )
        for source, replacement in replacements:
            text = text.replace(source, replacement)
        return text.replace("Player 1", "You")

    def _status_text(self) -> str:
        assert self.table is not None
        if self.bet_selection is not None:
            amount = self.bet_selection["amount"]
            if amount >= self.bet_selection["maximum"]:
                return f"All-in {amount}"
            action = "Raise" if self.table.current_bet else "Bet"
            return f"{action} {amount}"
        if self.table.match_over:
            return self.table.last_result
        if self.table.hand_over:
            return self.table.last_result or "Hand complete"
        actor = self.table.actor
        if actor is None:
            return ""
        if actor.is_bot:
            return f"{actor.name} thinking"
        legal = self.table.legal_actions(self.table.actor_index or 0)
        to_call = int(legal.get("to_call", 0))
        return f"{to_call} to call" if to_call else "Action on you"

    def _draw_card(self, x: int, y: int, card: Card, scale: float = 1.0) -> None:
        width = int(28 * scale)
        height = int(34 * scale)
        self._rect(
            x,
            y,
            x + width,
            y + height,
            fill=CARD_FACE,
            outline=CARD_EDGE,
        )
        fill = RED_CARD if card.suit in {"h", "s"} else BLACK_CARD
        pad = max(1, int(3 * scale))
        rank_size = max(7, min(int(width * 0.55), int(height * 0.5)))
        suit_space = max(5, height - rank_size - pad)
        suit_size = max(7, min(int(width * 0.95), int(height * 0.9), int(suit_space * 1.25)))
        self._text(
            x + pad,
            y + pad,
            anchor="nw",
            text=card.rank,
            fill=fill,
            font=("Menlo", rank_size, "bold"),
        )
        self._text(
            x + width,
            y + height + max(1, int(2 * scale)),
            anchor="se",
            text=SUIT_SYMBOLS[card.suit],
            fill=fill,
            font=("Apple Symbols", suit_size),
        )

    def _draw_empty_card(self, x: int, y: int, scale: float = 1.0) -> None:
        width = int(28 * scale)
        height = int(34 * scale)
        self._rect(
            x, y, x + width, y + height, fill=FELT_DARK, outline="#3e8669"
        )

    def _draw_card_back(self, x: int, y: int, scale: float = 1.0) -> None:
        width = int(28 * scale)
        height = int(38 * scale)
        self._rect(
            x, y, x + width, y + height, fill="#334654", outline="#8fb0bd"
        )
        inset = max(2, int(5 * scale))
        self._rect(
            x + inset,
            y + inset,
            x + width - inset,
            y + height - inset,
            outline="#8fb0bd",
        )


def import_hardware() -> tuple[object, object, object, tuple[object, object, object]]:
    try:
        import board
        import digitalio
        from adafruit_rgb_display import ili9341
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        message = (
            "Missing Raspberry Pi display/button dependencies.\n"
            f"Python executable: {sys.executable}\n"
            f"Import error: {exc!r}\n"
            "Install them on the Pi with:\n"
            "  python3 -m pip install adafruit-circuitpython-rgb-display pillow numpy"
        )
        raise MissingHardwareDependencies(message) from exc
    return board, digitalio, ili9341, (Image, ImageDraw, ImageFont)


def make_display(board: object, digitalio: object, ili9341: object) -> object:
    cs = digitalio.DigitalInOut(board.CE0)
    dc = digitalio.DigitalInOut(board.D25)
    rst = digitalio.DigitalInOut(board.D24)
    spi = board.SPI()
    return ili9341.ILI9341(
        spi,
        cs=cs,
        dc=dc,
        rst=rst,
        baudrate=32_000_000,
        width=WIDTH,
        height=HEIGHT,
        rotation=ROTATION,
    )


def make_buttons(board: object, digitalio: object) -> dict[str, object]:
    buttons = {}
    for key, pin in BUTTON_PINS.items():
        button = digitalio.DigitalInOut(getattr(board, f"D{pin}"))
        button.direction = digitalio.Direction.INPUT
        button.pull = digitalio.Pull.UP
        buttons[key] = button
    return buttons


def rgb888_to_rgb565(image: object) -> bytes:
    global NUMPY, NUMPY_IMPORT_ATTEMPTED
    if not NUMPY_IMPORT_ATTEMPTED:
        NUMPY_IMPORT_ATTEMPTED = True
        try:
            import numpy
        except ImportError:
            NUMPY = None
            print("NumPy not installed; using slower Python RGB565 conversion.")
            print("Install it with: python3 -m pip install numpy")
        else:
            NUMPY = numpy
    if NUMPY is not None:
        array = NUMPY.asarray(image.convert("RGB"), dtype=NUMPY.uint16)
        red = array[:, :, 0]
        green = array[:, :, 1]
        blue = array[:, :, 2]
        rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        return rgb565.byteswap().tobytes()

    data = image.convert("RGB").tobytes()
    output = bytearray(len(data) // 3 * 2)
    out_index = 0
    for index in range(0, len(data), 3):
        red = data[index]
        green = data[index + 1]
        blue = data[index + 2]
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        output[out_index] = value >> 8
        output[out_index + 1] = value & 0xFF
        out_index += 2
    return bytes(output)


def send_image_fast(display: object, image: object) -> None:
    block = getattr(display, "_block", None)
    if block is None:
        raise RuntimeError("display driver does not expose _block")
    width, height = image.size
    block(0, 0, width - 1, height - 1, rgb888_to_rgb565(image))


def send_image(display: object, image: object, image_module: object) -> None:
    transform = getattr(display, "_poker_transform", None)
    if transform == "rotate_90":
        display.image(image.transpose(image_module.Transpose.ROTATE_90))
        return
    if transform == "rotate_270":
        display.image(image.transpose(image_module.Transpose.ROTATE_270))
        return

    try:
        display.image(image)
        setattr(display, "_poker_transform", "none")
        return
    except ValueError as original_error:
        for name, transpose in (
            ("rotate_90", image_module.Transpose.ROTATE_90),
            ("rotate_270", image_module.Transpose.ROTATE_270),
        ):
            rotated = image.transpose(transpose)
            try:
                display.image(rotated)
                setattr(display, "_poker_transform", name)
                return
            except ValueError:
                pass
        raise original_error


class PiHandheldPokerApp(HandheldPokerCore):
    """Raspberry Pi ILI9341 + three active-low GPIO buttons."""

    def __init__(self) -> None:
        board, digitalio, ili9341, pil = import_hardware()
        self.Image, self.ImageDraw, self.ImageFont = pil
        self.display = make_display(board, digitalio, ili9341)
        self.buttons = make_buttons(board, digitalio)
        self.image = None
        self.draw = None
        self._font_cache: dict[tuple[int, bool, bool], object] = {}
        self._scheduled_jobs: dict[str, tuple[float, Callable[[], None]]] = {}
        self._job_counter = 0
        self._fast_transfer = True
        self._running = False
        now = time.monotonic()
        self._button_states = {key: self._is_pressed(button) for key, button in self.buttons.items()}
        self._button_candidates = self._button_states.copy()
        self._button_changed_at = {key: now for key in self.buttons}
        print("Running handheld poker on Raspberry Pi hardware.")
        print(f"Display size reported by driver: {self.display.width}x{self.display.height}")
        print("Buttons are active-low: GPIO -> button -> GND.")
        for key, pin in BUTTON_PINS.items():
            print(f"  {key}: GPIO{pin}")
        self._init_game_state()

    def after(self, delay_ms: int, callback: Callable[[], None]) -> str:
        self._job_counter += 1
        token = f"pi-after-{self._job_counter}"
        self._scheduled_jobs[token] = (time.monotonic() + delay_ms / 1000, callback)
        return token

    def after_cancel(self, job: str) -> None:
        self._scheduled_jobs.pop(job, None)

    def _clear(self) -> None:
        self.image = self.Image.new("RGB", (WIDTH, HEIGHT), BG)
        self.draw = self.ImageDraw.Draw(self.image)

    def _rect(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        fill: str | None = None,
        outline: str | None = None,
        width: int = 1,
    ) -> None:
        self.draw.rectangle(
            (int(x0), int(y0), int(x1), int(y1)),
            fill=self._color(fill),
            outline=self._color(outline),
            width=width,
        )

    def _oval(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        fill: str | None = None,
        outline: str | None = None,
        width: int = 1,
    ) -> None:
        self.draw.ellipse(
            (int(x0), int(y0), int(x1), int(y1)),
            fill=self._color(fill),
            outline=self._color(outline),
            width=width,
        )

    def _text(
        self,
        x: int,
        y: int,
        *,
        text: object = "",
        fill: str | None = None,
        font: object = None,
        anchor: str | None = None,
        width: int | None = None,
    ) -> None:
        loaded_font = self._font(font)
        lines = self._wrap_text(str(text), loaded_font, width)
        line_height = max(1, self._line_height(loaded_font))
        total_height = line_height * len(lines)
        top = self._text_top(y, total_height, anchor)
        for index, line in enumerate(lines):
            line_width, _line_height = self._text_size(line, loaded_font)
            left = self._text_left(x, line_width, anchor)
            self.draw.text(
                (int(left), int(top + index * line_height)),
                line,
                font=loaded_font,
                fill=self._color(fill) or INK,
            )

    def _present(self) -> None:
        if self._fast_transfer:
            try:
                send_image_fast(self.display, self.image)
                return
            except RuntimeError as exc:
                print(f"Fast display transfer unavailable: {exc}. Falling back.")
                self._fast_transfer = False
        send_image(self.display, self.image, self.Image)

    @staticmethod
    def _color(color: str | None) -> str | None:
        return None if color in {None, ""} else color

    def _font(self, font_spec: object) -> object:
        size = 12
        bold = False
        mono = False
        if isinstance(font_spec, tuple):
            for part in font_spec:
                if isinstance(part, int):
                    size = part
                elif isinstance(part, str):
                    lowered = part.lower()
                    if lowered == "bold":
                        bold = True
                    elif lowered in {"numbers", "mono", "monospace"}:
                        mono = True
        key = (size, bold, mono)
        if key not in self._font_cache:
            if mono:
                candidates = (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                )
            else:
                candidates = (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                )
                if not bold:
                    candidates = tuple(reversed(candidates))
            for path in candidates:
                try:
                    self._font_cache[key] = self.ImageFont.truetype(path, size)
                    break
                except OSError:
                    pass
            else:
                self._font_cache[key] = self.ImageFont.load_default()
        return self._font_cache[key]

    def _wrap_text(self, text: str, font: object, width: int | None) -> list[str]:
        if width is None or width <= 0:
            return text.splitlines() or [""]
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            current = ""
            for word in paragraph.split(" "):
                candidate = word if not current else f"{current} {word}"
                if self._text_size(candidate, font)[0] <= width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                if self._text_size(word, font)[0] <= width:
                    current = word
                    continue
                pieces = self._break_word(word, font, width)
                lines.extend(pieces[:-1])
                current = pieces[-1] if pieces else ""
            lines.append(current)
        return lines or [""]

    def _break_word(self, word: str, font: object, width: int) -> list[str]:
        pieces: list[str] = []
        current = ""
        for char in word:
            candidate = current + char
            if current and self._text_size(candidate, font)[0] > width:
                pieces.append(current)
                current = char
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces

    def _text_size(self, text: str, font: object) -> tuple[int, int]:
        try:
            bbox = self.draw.textbbox((0, 0), text, font=font)
        except AttributeError:
            return self.draw.textsize(text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def _line_height(self, font: object) -> int:
        _width, height = self._text_size("Ag", font)
        return height + 2

    @staticmethod
    def _text_top(y: int, total_height: int, anchor: str | None) -> float:
        if anchor in {"nw", "n", "ne"}:
            return y
        if anchor in {"sw", "s", "se"}:
            return y - total_height
        return y - total_height / 2

    @staticmethod
    def _text_left(x: int, line_width: int, anchor: str | None) -> float:
        if anchor in {"nw", "w", "sw"}:
            return x
        if anchor in {"ne", "e", "se"}:
            return x - line_width
        return x - line_width / 2

    def mainloop(self) -> None:
        self._running = True
        try:
            while self._running:
                self._poll_buttons()
                self._run_due_callbacks()
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\nHandheld poker stopped.")
        finally:
            self._close_buttons()

    @staticmethod
    def _is_pressed(button: object) -> bool:
        return not bool(button.value)

    def _poll_buttons(self) -> None:
        now = time.monotonic()
        for key, button in self.buttons.items():
            pressed = self._is_pressed(button)
            if pressed != self._button_candidates[key]:
                self._button_candidates[key] = pressed
                self._button_changed_at[key] = now
                continue
            if pressed == self._button_states[key]:
                continue
            if now - self._button_changed_at[key] < DEBOUNCE_SECONDS:
                continue
            self._button_states[key] = pressed
            if pressed:
                self._key_down(key)
            else:
                self._key_up(key)

    def _run_due_callbacks(self) -> None:
        now = time.monotonic()
        due = [
            (scheduled_at, token)
            for token, (scheduled_at, _callback) in self._scheduled_jobs.items()
            if scheduled_at <= now
        ]
        for _scheduled_at, token in sorted(due):
            job = self._scheduled_jobs.pop(token, None)
            if job is not None:
                _scheduled_at, callback = job
                callback()

    def _close_buttons(self) -> None:
        for button in self.buttons.values():
            deinit = getattr(button, "deinit", None)
            if callable(deinit):
                deinit()


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("handheld_poker.py does not accept options.", file=sys.stderr)
        raise SystemExit(2)
    try:
        PiHandheldPokerApp().mainloop()
    except MissingHardwareDependencies as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
