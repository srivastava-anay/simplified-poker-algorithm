"""320x240 handheld poker demo using three keyboard buttons."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from textwrap import wrap

from .cards import Card
from .table import MultiplayerTable, TableEvent

WIDTH = 320
HEIGHT = 240
BUTTON_H = 40
GUTTER = 6
LOG_W = 84
LOG_X = WIDTH - LOG_W - GUTTER
PLAY_X = GUTTER
PLAY_W = LOG_X - PLAY_X - GUTTER

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
RED_CARD = "#a73535"
SUIT_SYMBOLS = {
    "s": "\u2660",
    "h": "\u2665",
    "d": "\u2666",
    "c": "\u2663",
}


@dataclass(frozen=True)
class SoftButton:
    key: str
    label: str
    enabled: bool


class HandheldPokerApp(tk.Tk):
    """Small-screen poker interface for laptop testing before Pi hardware."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Handheld Poker Demo")
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)
        self.configure(bg=BG)

        self.table: MultiplayerTable | None = None
        self.bot_count = 1
        self.mode = "setup"
        self.canvas = tk.Canvas(
            self,
            width=WIDTH,
            height=HEIGHT,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()
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

        for key, name in (("j", "J"), ("k", "K"), ("l", "L")):
            self.bind(f"<KeyPress-{key}>", lambda _event, value=name: self._key_down(value))
            self.bind(f"<KeyPress-{key.upper()}>", lambda _event, value=name: self._key_down(value))
            self.bind(f"<KeyRelease-{key}>", lambda _event, value=name: self._key_up(value))
            self.bind(f"<KeyRelease-{key.upper()}>", lambda _event, value=name: self._key_up(value))
        self.bind("<KeyPress-space>", lambda _event: self._key_down("K"))
        self.bind("<KeyRelease-space>", lambda _event: self._key_up("K"))
        self.focus_force()

        self._refresh()

    def _start_hand(self) -> None:
        if self.table is None:
            simulations = max(120, 450 - self.bot_count * 40)
            self.table = MultiplayerTable(
                human_players=1,
                bot_players=self.bot_count,
                starting_stack=500,
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
                self.bot_count = min(7, self.bot_count + 1)
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
        return 1

    def _flash(self, text: str) -> None:
        self._refresh(status=text[:28])
        self.after(900, self._refresh)

    def _refresh(self, status: str | None = None) -> None:
        if self.mode == "setup":
            self._set_setup_buttons()
            self.canvas.delete("all")
            self._draw_setup()
            self._draw_buttons()
            return
        if self.table is None:
            return
        self._pull_events()
        self._set_soft_buttons()
        self.canvas.delete("all")
        self._draw_background()
        self._draw_header()
        self._draw_opponents()
        self._draw_board()
        self._draw_hero(status)
        self._draw_log()
        self._draw_buttons()

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
            SoftButton("L", "+ Bots", self.bot_count < 7),
        ]

    def _draw_setup(self) -> None:
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill=BG, outline="")
        self.canvas.create_rectangle(
            10, 18, WIDTH - 10, HEIGHT - BUTTON_H - 12, fill=PANEL, outline="#2d3b40"
        )
        self.canvas.create_text(
            72,
            54,
            text="POKER",
            fill=GOLD,
            font=("Menlo", 20, "bold"),
        )
        self.canvas.create_text(
            72,
            84,
            text="How many bots?",
            fill=INK,
            font=("Menlo", 12, "bold"),
        )
        self.canvas.create_oval(190, 42, 276, 128, fill=FELT, outline=GOLD, width=2)
        self.canvas.create_text(
            233,
            73,
            text=str(self.bot_count),
            fill="white",
            font=("Menlo", 32, "bold"),
        )
        label = "bot" if self.bot_count == 1 else "bots"
        self.canvas.create_text(
            233,
            104,
            text=label,
            fill=MUTED,
            font=("Menlo", 11, "bold"),
        )
        total = self.bot_count + 1
        self.canvas.create_text(
            WIDTH // 2,
            158,
            text=f"{total} total players",
            fill=INK,
            font=("Menlo", 10),
        )

    def _draw_background(self) -> None:
        assert self.table is not None
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill=BG, outline="")
        self.canvas.create_rectangle(
            PLAY_X, 30, PLAY_X + PLAY_W, 158, fill=FELT, outline=FELT_DARK, width=3
        )
        self.canvas.create_rectangle(
            PLAY_X + 8, 38, PLAY_X + PLAY_W - 8, 150, outline="#2e8060", width=1
        )
        self.canvas.create_rectangle(
            LOG_X, 30, WIDTH - GUTTER, HEIGHT - BUTTON_H - 4, fill=PANEL, outline="#263438"
        )

    def _draw_header(self) -> None:
        assert self.table is not None
        title = f"HAND {self.table.hand_number}"
        stage = self.table.stage.value.upper()
        self.canvas.create_text(
            10, 10, anchor="nw", text=title, fill=GOLD, font=("Menlo", 10, "bold")
        )
        self.canvas.create_text(
            PLAY_X + PLAY_W // 2,
            10,
            anchor="n",
            text=f"POT {self.table.pot}",
            fill=INK,
            font=("Menlo", 9, "bold"),
        )
        self.canvas.create_text(
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
        if count <= 4:
            width = 48 if count > 2 else 60
            height = 34
            gap = 5
            total = count * width + (count - 1) * gap
            start_x = PLAY_X + (PLAY_W - total) // 2
            return [(start_x + i * (width + gap), 42, width, height) for i in range(count)]

        width = 37
        height = 29
        gap = 4
        top_count = 4 if count == 7 else 3
        bottom_count = count - top_count
        rows = [(top_count, 38), (bottom_count, 72)]
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
        self.canvas.create_rectangle(
            x, y, x + width, y + height, fill=fill, outline=outline
        )
        self.canvas.create_text(
            x + 4,
            y + 4,
            anchor="nw",
            text=f"B{bot_index}",
            fill=GOLD,
            font=("Menlo", 7 if compact else 8, "bold"),
        )
        if self.table.hand_over and bot.in_hand and bot.hole_cards:
            self._draw_showdown_cards(x, y, width, height, bot.hole_cards, compact)
        else:
            self.canvas.create_text(
                x + 4,
                y + height - 4,
                anchor="sw",
                text=detail,
                fill=INK,
                font=("Menlo", 6 if compact else 7),
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
        self.canvas.create_rectangle(
            x0, y0, x0 + w, y0 + h, fill="#253034", outline="#526065"
        )
        self.canvas.create_text(
            x0 + w // 2,
            y0 + h // 2,
            text=f"B{bot_index} F",
            fill="#a7b3b5",
            font=("Menlo", 5 if compact else 6, "bold"),
        )

    def _draw_board(self) -> None:
        assert self.table is not None
        board = self.table.board
        total_players = len(self.table.players)
        scale = 0.82 if total_players > 4 else 0.88
        card_w = int(28 * scale)
        gap = 10 if total_players > 4 else 15
        total_w = 5 * card_w + 4 * gap
        start_x = PLAY_X + (PLAY_W - total_w) // 2
        y = 112 if total_players > 4 else 106
        for index in range(5):
            x = start_x + index * (card_w + gap)
            if index < len(board):
                self._draw_card(x, y, board[index], scale=scale)
            else:
                self._draw_empty_card(x, y, scale=scale)

    def _draw_hero(self, status: str | None) -> None:
        assert self.table is not None
        hero = self.table.players[0]
        self.canvas.create_rectangle(
            PLAY_X, 162, PLAY_X + PLAY_W, HEIGHT - BUTTON_H - 4, fill=PANEL, outline="#263438"
        )
        self.canvas.create_text(
            13, 168, anchor="nw", text="YOU", fill=GOLD, font=("Menlo", 8, "bold")
        )
        chips = f"{hero.stack}"
        if hero.street_contribution:
            chips += f"/{hero.street_contribution}"
        self.canvas.create_text(
            13, 187, anchor="nw", text=chips, fill=INK, font=("Menlo", 7)
        )

        if hero.hole_cards:
            self._draw_card(58, 168, hero.hole_cards[0], scale=0.82)
            self._draw_card(88, 168, hero.hole_cards[1], scale=0.82)
        message = status or self._status_text()
        self.canvas.create_text(
            123,
            170,
            anchor="nw",
            text=message,
            fill=INK,
            font=("Menlo", 8 if len(message) < 22 else 7, "bold"),
            width=92,
        )

    def _draw_log(self) -> None:
        assert self.table is not None
        line_height = 8
        top_y = 38
        max_y = HEIGHT - BUTTON_H - 8
        max_lines = max(1, (max_y - top_y) // line_height)
        blocks: list[tuple[list[str], str]] = []
        for event in self.visible_events:
            if event.kind == "street":
                continue
            text = self._format_log_text(event.text)
            fill = GOLD if event.kind == "result" else "#d7e0da"
            wrapped = wrap(text, width=17, break_long_words=True) or [""]
            blocks.append((wrapped, fill))

        visible_blocks = self._fit_log_blocks(blocks, max_lines)
        used_lines = sum(len(lines) for lines, _fill in visible_blocks)
        y = max(top_y, max_y - used_lines * line_height)
        for lines, fill in visible_blocks:
            for text in lines:
                self.canvas.create_text(
                    LOG_X + 6,
                    y,
                    anchor="nw",
                    text=text,
                    fill=fill,
                    font=("Menlo", 6),
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

    def _draw_buttons(self) -> None:
        y = HEIGHT - BUTTON_H
        colors = {"J": RED, "K": GREEN, "L": BLUE}
        button_w = WIDTH // 3
        for index, button in enumerate(self.soft_buttons):
            x0 = index * button_w
            x1 = WIDTH if index == 2 else x0 + button_w
            fill = colors[button.key] if button.enabled else DISABLED
            self.canvas.create_rectangle(x0, y, x1, HEIGHT, fill=fill, outline=BG)
            self.canvas.create_text(
                (x0 + x1) // 2,
                y + 13,
                text=button.key,
                fill="#101719",
                font=("Menlo", 9, "bold"),
            )
            self.canvas.create_text(
                (x0 + x1) // 2,
                y + 31,
                text=button.label,
                fill="white" if button.enabled else "#91a0a3",
                font=("Menlo", 8, "bold"),
                width=button_w - 4,
            )

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
        self.canvas.create_rectangle(
            x, y, x + width, y + height, fill=CARD_FACE, outline=CARD_EDGE
        )
        fill = RED_CARD if card.suit in {"h", "d"} else BLACK_CARD
        pad = max(1, int(3 * scale))
        rank_size = max(7, min(int(width * 0.55), int(height * 0.5)))
        suit_space = max(5, height - rank_size - pad)
        suit_size = max(8, min(int(width * 1.35), int(height * 1.25), int(suit_space * 1.9)))
        self.canvas.create_text(
            x + pad,
            y + pad,
            anchor="nw",
            text=card.rank,
            fill=fill,
            font=("Menlo", rank_size, "bold"),
        )
        self.canvas.create_text(
            x + width + max(1, int(1 * scale)),
            y + height + max(3, int(11 * scale)),
            anchor="se",
            text=SUIT_SYMBOLS[card.suit],
            fill=fill,
            font=("Apple Symbols", suit_size),
        )

    def _draw_empty_card(self, x: int, y: int, scale: float = 1.0) -> None:
        width = int(28 * scale)
        height = int(34 * scale)
        self.canvas.create_rectangle(
            x, y, x + width, y + height, fill=FELT_DARK, outline="#3e8669"
        )

    def _draw_card_back(self, x: int, y: int, scale: float = 1.0) -> None:
        width = int(28 * scale)
        height = int(38 * scale)
        self.canvas.create_rectangle(
            x, y, x + width, y + height, fill="#334654", outline="#8fb0bd"
        )
        inset = max(2, int(5 * scale))
        self.canvas.create_rectangle(
            x + inset,
            y + inset,
            x + width - inset,
            y + height - inset,
            outline="#8fb0bd",
        )


def main() -> None:
    HandheldPokerApp().mainloop()
