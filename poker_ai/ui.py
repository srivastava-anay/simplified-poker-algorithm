"""Tkinter desktop interface for multiplayer local poker."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from .cards import Card
from .table import MultiplayerTable, TablePlayer

FELT = "#176b45"
DARK_FELT = "#0f4f34"
GOLD = "#f2c14e"
CREAM = "#f7f1df"
RED = "#d94b4b"
CARD_BACK = "🂠"
INK = "#202722"
INPUT_BG = "#ffffff"


class PokerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Texas Hold'em Poker AI")
        self.geometry("1100x760")
        self.minsize(900, 650)
        self.configure(bg=DARK_FELT)
        self.table: MultiplayerTable | None = None
        self.table_frame: PokerTableFrame | None = None
        self.show_setup()

    def show_setup(self) -> None:
        if self.table_frame is not None:
            self.table_frame.destroy()
            self.table_frame = None
        SetupFrame(self, self.start_game).pack(fill="both", expand=True)

    def start_game(
        self,
        humans: int,
        bots: int,
        stack: int,
        small_blind: int,
        big_blind: int,
        simulations: int,
    ) -> None:
        for child in self.winfo_children():
            child.destroy()
        try:
            self.table = MultiplayerTable(
                humans,
                bots,
                stack,
                small_blind,
                big_blind,
                simulations,
            )
        except ValueError as exc:
            messagebox.showerror("Invalid game", str(exc))
            self.show_setup()
            return
        self.table_frame = PokerTableFrame(self, self.table, self.show_setup)
        self.table_frame.pack(fill="both", expand=True)
        self.table_frame.start_hand()


class SetupFrame(tk.Frame):
    def __init__(self, master: tk.Misc, on_start) -> None:
        super().__init__(master, bg=DARK_FELT)
        self.on_start = on_start
        self.humans = tk.IntVar(value=1)
        self.bots = tk.IntVar(value=1)
        self.stack = tk.IntVar(value=1000)
        self.small_blind = tk.IntVar(value=5)
        self.big_blind = tk.IntVar(value=10)
        self.simulations = tk.IntVar(value=2000)
        self._build()

    def _build(self) -> None:
        panel = tk.Frame(self, bg=CREAM, padx=42, pady=34)
        panel.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            panel,
            text="Texas Hold'em",
            font=("Helvetica", 30, "bold"),
            bg=CREAM,
            fg=DARK_FELT,
        ).grid(row=0, column=0, columnspan=2, pady=(0, 4))
        tk.Label(
            panel,
            text="Local table vs Monte Carlo poker bots",
            font=("Helvetica", 13),
            bg=CREAM,
            fg="#555555",
        ).grid(row=1, column=0, columnspan=2, pady=(0, 26))

        fields = [
            ("Real players", self.humans, 1, 7),
            ("Bots", self.bots, 0, 7),
            ("Starting chips", self.stack, 100, 100000),
            ("Small blind", self.small_blind, 1, 1000),
            ("Big blind", self.big_blind, 2, 2000),
            ("AI simulations", self.simulations, 100, 20000),
        ]
        for row, (label, variable, low, high) in enumerate(fields, start=2):
            tk.Label(
                panel, text=label, font=("Helvetica", 12), bg=CREAM, anchor="w"
            ).grid(row=row, column=0, sticky="w", padx=(0, 28), pady=6)
            tk.Spinbox(
                panel,
                from_=low,
                to=high,
                textvariable=variable,
                width=10,
                justify="center",
                bg=INPUT_BG,
                fg=INK,
                buttonbackground="#d9d3c4",
                insertbackground=INK,
                readonlybackground=INPUT_BG,
                selectbackground=DARK_FELT,
                selectforeground="white",
                relief="solid",
                highlightthickness=1,
                highlightbackground="#9b8d6e",
            ).grid(row=row, column=1, pady=6)

        tk.Label(
            panel,
            text="2–8 total seats. Multiple humans use private pass-and-play turns.",
            bg=CREAM,
            fg="#666666",
            wraplength=380,
        ).grid(row=8, column=0, columnspan=2, pady=(18, 14))
        tk.Button(
            panel,
            text="Start game",
            command=self._start,
            font=("Helvetica", 14, "bold"),
            bg=GOLD,
            fg="#1f1f1f",
            activebackground="#ffd66b",
            activeforeground="#1f1f1f",
            relief="flat",
            padx=28,
            pady=10,
        ).grid(row=9, column=0, columnspan=2)

    def _start(self) -> None:
        try:
            values = (
                self.humans.get(),
                self.bots.get(),
                self.stack.get(),
                self.small_blind.get(),
                self.big_blind.get(),
                self.simulations.get(),
            )
        except tk.TclError:
            messagebox.showerror("Invalid game", "Please enter whole numbers.")
            return
        total = values[0] + values[1]
        if not 2 <= total <= 8:
            messagebox.showerror("Invalid game", "Choose between 2 and 8 total players.")
            return
        self.on_start(*values)


class PokerTableFrame(tk.Frame):
    def __init__(self, master: tk.Misc, table: MultiplayerTable, on_new_game) -> None:
        super().__init__(master, bg=DARK_FELT)
        self.table = table
        self.on_new_game = on_new_game
        self.seat_frames: list[tk.Frame] = []
        self.revealed_human: int | None = None
        self.event_cursor = 0
        self.amount = tk.IntVar(value=20)
        self.status = tk.StringVar()
        self.board_text = tk.StringVar()
        self.pot_text = tk.StringVar()
        self.turn_text = tk.StringVar()
        self._bot_job: str | None = None
        self._build()

    def _build(self) -> None:
        toolbar = tk.Frame(self, bg="#18211d", pady=8)
        toolbar.pack(fill="x")
        tk.Label(
            toolbar,
            text="POKER AI TABLE",
            bg="#18211d",
            fg=GOLD,
            font=("Helvetica", 15, "bold"),
        ).pack(side="left", padx=14)
        tk.Button(
            toolbar,
            text="New setup",
            command=self._confirm_new_game,
            bg="#343f3a",
            fg="white",
            activebackground="#48564f",
            activeforeground="white",
            relief="flat",
        ).pack(side="right", padx=12)

        body = tk.Frame(self, bg=DARK_FELT)
        body.pack(fill="both", expand=True, padx=12, pady=10)
        left = tk.Frame(body, bg=DARK_FELT)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg="#17231e", width=275)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        self.table_area = tk.Frame(left, bg=FELT, highlightbackground=GOLD, highlightthickness=2)
        self.table_area.pack(fill="both", expand=True)
        center = tk.Frame(self.table_area, bg=FELT)
        center.place(relx=0.5, rely=0.49, anchor="center")
        tk.Label(
            center,
            textvariable=self.pot_text,
            bg=FELT,
            fg=GOLD,
            font=("Helvetica", 17, "bold"),
        ).pack(pady=5)
        tk.Label(
            center,
            textvariable=self.board_text,
            bg=FELT,
            fg="white",
            font=("Menlo", 24, "bold"),
        ).pack(pady=8)
        tk.Label(
            center,
            textvariable=self.turn_text,
            bg=FELT,
            fg=CREAM,
            font=("Helvetica", 12),
        ).pack(pady=4)

        self._build_seats()
        self._build_controls(left)

        tk.Label(
            right,
            text="TABLE LOG",
            bg="#17231e",
            fg=GOLD,
            font=("Helvetica", 12, "bold"),
        ).pack(pady=(12, 4))
        self.log = tk.Text(
            right,
            bg="#101713",
            fg="#e8e8e8",
            insertbackground="white",
            font=("Menlo", 10),
            wrap="word",
            state="disabled",
            padx=8,
            pady=8,
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_seats(self) -> None:
        positions = [
            (0.50, 0.88),
            (0.18, 0.78),
            (0.08, 0.48),
            (0.20, 0.16),
            (0.50, 0.08),
            (0.80, 0.16),
            (0.92, 0.48),
            (0.82, 0.78),
        ]
        for index, player in enumerate(self.table.players):
            frame = tk.Frame(
                self.table_area,
                bg="#f3ead4",
                width=158,
                height=92,
                highlightthickness=2,
                highlightbackground="#9b8d6e",
            )
            frame.place(
                relx=positions[index][0],
                rely=positions[index][1],
                anchor="center",
            )
            frame.pack_propagate(False)
            self.seat_frames.append(frame)
            tk.Label(
                frame,
                name="name",
                text=player.name,
                bg="#f3ead4",
                fg="#222222",
                font=("Helvetica", 11, "bold"),
            ).pack(pady=(5, 0))
            tk.Label(
                frame,
                name="cards",
                text=f"{CARD_BACK} {CARD_BACK}",
                bg="#f3ead4",
                fg="#1d3557",
                font=("Menlo", 16, "bold"),
            ).pack()
            tk.Label(
                frame,
                name="details",
                bg="#f3ead4",
                fg="#444444",
                font=("Helvetica", 10),
            ).pack()

    def _build_controls(self, parent: tk.Frame) -> None:
        controls = tk.Frame(parent, bg="#17231e", pady=9)
        controls.pack(fill="x", pady=(10, 0))
        tk.Label(
            controls,
            textvariable=self.status,
            bg="#17231e",
            fg="white",
            font=("Helvetica", 12, "bold"),
        ).pack(side="left", padx=12)
        self.fold_button = self._action_button(controls, "Fold", self._fold, RED)
        self.check_button = self._action_button(controls, "Check", self._check_call, "#3d8f65")
        self.raise_button = self._action_button(controls, "Bet", self._bet_raise, "#3274a1")
        self.all_in_button = self._action_button(controls, "All-in", self._all_in, "#7b4da3")
        self.amount_box = tk.Spinbox(
            controls,
            textvariable=self.amount,
            width=8,
            justify="center",
            font=("Helvetica", 12),
            bg=INPUT_BG,
            fg=INK,
            buttonbackground="#d9d3c4",
            insertbackground=INK,
            readonlybackground=INPUT_BG,
            disabledbackground="#d8ddd9",
            disabledforeground="#59625d",
            selectbackground=DARK_FELT,
            selectforeground="white",
            relief="solid",
            highlightthickness=1,
            highlightbackground="#8b9991",
        )
        self.amount_box.pack(side="right", padx=8)
        self.next_button = tk.Button(
            controls,
            text="Next hand",
            command=self.start_hand,
            bg=GOLD,
            fg=INK,
            activebackground="#ffd66b",
            activeforeground=INK,
            relief="flat",
            font=("Helvetica", 11, "bold"),
            padx=14,
        )

    @staticmethod
    def _action_button(parent, text, command, color):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg="white",
            activeforeground="white",
            relief="flat",
            width=9,
            font=("Helvetica", 11, "bold"),
        )
        button.pack(side="right", padx=4)
        return button

    def start_hand(self) -> None:
        self.revealed_human = None
        self.event_cursor = 0
        self.next_button.pack_forget()
        self.table.start_hand()
        self._refresh()
        self._continue_turn()

    def _continue_turn(self) -> None:
        self._refresh()
        if self.table.hand_over:
            self.status.set(self.table.last_result)
            self._disable_actions()
            if not self.table.match_over:
                self.next_button.pack(side="right", padx=10)
            return
        actor = self.table.actor
        if actor is None:
            return
        if actor.is_bot:
            self.status.set(f"{actor.name} is thinking…")
            self._disable_actions()
            self._bot_job = self.after(650, self._run_bot)
        else:
            index = self.table.actor_index
            assert index is not None
            self.revealed_human = None
            self._disable_actions()
            self.after(100, lambda: self._request_reveal(index))

    def _request_reveal(self, index: int) -> None:
        player = self.table.players[index]
        dialog = tk.Toplevel(self)
        dialog.title("Private turn")
        dialog.configure(bg=DARK_FELT)
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("430x230")
        tk.Label(
            dialog,
            text=f"Pass the computer to {player.name}",
            bg=DARK_FELT,
            fg=GOLD,
            font=("Helvetica", 20, "bold"),
        ).pack(pady=(38, 12))
        tk.Label(
            dialog,
            text="Other players should look away.\nReveal cards when ready.",
            bg=DARK_FELT,
            fg="white",
            font=("Helvetica", 12),
        ).pack()

        def reveal() -> None:
            self.revealed_human = index
            dialog.destroy()
            self._enable_human_actions()
            self._refresh()

        tk.Button(
            dialog,
            text="Reveal my cards",
            command=reveal,
            bg=GOLD,
            fg=INK,
            activebackground="#ffd66b",
            activeforeground=INK,
            relief="flat",
            font=("Helvetica", 13, "bold"),
            padx=22,
            pady=8,
        ).pack(pady=22)
        dialog.protocol("WM_DELETE_WINDOW", reveal)

    def _enable_human_actions(self) -> None:
        index = self.table.actor_index
        if index is None:
            return
        legal = self.table.legal_actions(index)
        to_call = int(legal["to_call"])
        self.status.set(
            f"{self.table.players[index].name}'s turn — "
            + (f"{to_call} to call" if to_call else "check is free")
        )
        self.fold_button.config(state="normal" if to_call else "disabled")
        self.check_button.config(
            text=f"Call {to_call}" if to_call else "Check", state="normal"
        )
        can_aggress = bool(legal["can_bet"]) or bool(legal["can_raise"])
        self.raise_button.config(
            text="Raise" if legal["can_raise"] else "Bet",
            state="normal" if can_aggress else "disabled",
        )
        self.all_in_button.config(state="normal" if can_aggress else "disabled")
        minimum = int(legal["minimum_total"])
        maximum = int(legal["maximum_total"])
        self.amount.set(minimum)
        self.amount_box.config(from_=minimum, to=maximum, state="normal" if can_aggress else "disabled")

    def _disable_actions(self) -> None:
        for button in (
            self.fold_button,
            self.check_button,
            self.raise_button,
            self.all_in_button,
        ):
            button.config(state="disabled")
        self.amount_box.config(state="disabled")

    def _human_action(self, action: str, amount: int | None = None) -> None:
        index = self.table.actor_index
        if index is None:
            return
        try:
            self.table.act(index, action, amount)
        except ValueError as exc:
            messagebox.showerror("Invalid action", str(exc))
            return
        self.revealed_human = None
        self._continue_turn()

    def _fold(self) -> None:
        self._human_action("fold")

    def _check_call(self) -> None:
        index = self.table.actor_index
        if index is None:
            return
        action = "call" if int(self.table.legal_actions(index)["to_call"]) else "check"
        self._human_action(action)

    def _bet_raise(self) -> None:
        index = self.table.actor_index
        if index is None:
            return
        try:
            amount = self.amount.get()
        except tk.TclError:
            messagebox.showerror("Invalid amount", "Enter a whole number of chips.")
            return
        action = "raise" if self.table.current_bet else "bet"
        self._human_action(action, amount)

    def _all_in(self) -> None:
        self._human_action("allin")

    def _run_bot(self) -> None:
        self._bot_job = None
        try:
            self.table.bot_act()
        except Exception as exc:
            messagebox.showerror("Bot error", str(exc))
            return
        self._continue_turn()

    def _refresh(self) -> None:
        self.pot_text.set(f"Pot: {self.table.pot}")
        self.board_text.set(self._format_cards(self.table.board) or "—  —  —  —  —")
        actor = self.table.actor
        self.turn_text.set(
            f"{self.table.stage.value.title()} • "
            + (f"Action: {actor.name}" if actor else "Hand complete")
        )
        showdown = self.table.hand_over and len(self.table.board) == 5
        for index, (player, frame) in enumerate(zip(self.table.players, self.seat_frames)):
            is_actor = index == self.table.actor_index
            frame.config(highlightbackground=GOLD if is_actor else "#9b8d6e")
            suffix = " • D" if index == self.table.dealer_index else ""
            frame.nametowidget("name").config(text=player.name + suffix)
            visible = showdown and player.in_hand
            visible = visible or (not player.is_bot and self.revealed_human == index)
            cards = (
                self._format_cards(player.hole_cards or ())
                if visible
                else f"{CARD_BACK} {CARD_BACK}"
            )
            frame.nametowidget("cards").config(text=cards)
            states = []
            if player.folded and player.hole_cards is not None:
                states.append("folded")
            if player.all_in:
                states.append("all-in")
            detail = f"{player.stack} chips"
            if player.street_contribution:
                detail += f" • bet {player.street_contribution}"
            if states:
                detail += " • " + ", ".join(states)
            frame.nametowidget("details").config(text=detail)
        self._append_events()

    def _append_events(self) -> None:
        if self.event_cursor >= len(self.table.events):
            return
        self.log.config(state="normal")
        for event in self.table.events[self.event_cursor :]:
            self.log.insert("end", event.text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        self.event_cursor = len(self.table.events)

    def _confirm_new_game(self) -> None:
        if messagebox.askyesno("New setup", "Leave this table and choose new players?"):
            if self._bot_job is not None:
                self.after_cancel(self._bot_job)
            self.on_new_game()

    @staticmethod
    def _format_cards(cards: tuple[Card, ...] | list[Card]) -> str:
        suits = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
        return "  ".join(f"{card.rank}{suits[card.suit]}" for card in cards)


def main() -> None:
    PokerApp().mainloop()
