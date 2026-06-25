# Texas Hold'em Poker AI

A readable, modular decision engine that estimates showdown equity with Monte
Carlo simulation, compares it with pot odds, tracks simple opponent tendencies,
and selects `fold`, `check`, `call`, `bet`, or `raise`.

This is a decision aid and learning project, not a solved-poker/GTO engine. It
uses action-weighted opponent ranges rather than assuming every opponent always
holds two uniformly random cards.

## Project layout

- `poker_ai/cards.py` — card parsing and deck representation
- `poker_ai/evaluator.py` — `treys` hand scoring, Monte Carlo equity, draw hints
- `poker_ai/strategy.py` — pot odds, value actions, sizing, controlled bluffing
- `poker_ai/opponents.py` — lightweight opponent action profiles
- `poker_ai/cli.py` — runnable command-line demo
- `poker_ai/game.py` — interactive heads-up game loop
- `poker_ai/table.py` — reusable 2–8 seat multiplayer state machine
- `poker_ai/ui.py` — Tkinter graphical poker table
- `play_poker.py` — standalone game launcher
- `poker_ui.py` — graphical game launcher
- `tests/` — focused unit tests

## Install on macOS

Python 3.10 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Run the demo

Running without arguments uses a built-in flop example:

```bash
poker-ai
```

Or provide a situation:

```bash
poker-ai \
  --hole As Kh \
  --board Qs Jh 2c \
  --pot 100 \
  --call 20 \
  --opponents 1 \
  --stage flop \
  --position late \
  --action villain:bet:20 \
  --simulations 5000 \
  --seed 42
```

Use `--can-check` only when `--call 0`. Card notation is rank plus suit:
`As`, `Td`, `7c`, `2h`; suits are spades, hearts, diamonds, clubs.

The JSON result includes the chosen action, amount, equity, pot odds, bluff
flag, and rationale. For a raise, `amount` is the total additional chips the
bot should put in now: call plus raise increment. Sizing is pot-relative because
stack sizes and table minimums are not among the current inputs. A production
integration should clamp it to the legal minimum raise and effective stack.

## Play against the AI

After installing the project, start an interactive terminal match:

```bash
source .venv/bin/activate
python play_poker.py
```

The installed command works too:

```bash
play-poker
```

The game first asks how many bots you want to face:

```text
How many bots do you want to play against? [1-7]:
```

You can also skip the prompt:

```bash
play-poker --bots 4
```

Useful options:

```bash
python play_poker.py \
  --bots 3 \
  --stack 1000 \
  --small-blind 5 \
  --big-blind 10 \
  --simulations 3000
```

Available commands during a hand:

- `fold` or `f`
- `check` or `x`
- `call` or `c`
- `bet 30` — bet 30 chips
- `raise 80` — raise to 80 total chips on the current betting street
- `all-in`
- `quit`

The script deals a shuffled deck, rotates the dealer and blinds, runs all four
betting streets, tracks every stack, supports side pots, and uses `treys` for
showdowns. On each bot
turn it passes the bot's cards, visible board, pot, call price, your observed
actions, stage, and position into `StrategyEngine`. The engine runs fresh Monte
Carlo trials, compares equity with pot odds, and returns a legal action and
pot-relative size. The game clamps that size to the effective stack and the
minimum legal raise.

This is intentionally a compact practice game. It supports one human versus
one to seven bots and whole-number chips. It does not implement antes, rake,
time controls, or networked players.

## Stronger Pi-friendly strategy

The bots use several inexpensive improvements instead of a large neural network
or solver:

- position- and player-count-aware preflop opening/continuing charts
- weighted opponent ranges based on checks, calls, bets, raises, and sizing
- estimated EV for folding, calling, and two or three candidate bet sizes
- learned fold-to-bet, fold-to-raise, aggression, and participation tendencies
- board texture, draw strength, effective stacks, and basic fold equity
- tight-aggressive, loose-aggressive, balanced, and tricky bot personalities
- dynamic simulation budgets and a small equity-result cache

`--simulations` is now the maximum budget rather than the amount used for every
decision. Preflop charts need relatively few trials, while difficult flop spots
use more. Turn, river, and large-field decisions automatically use adjusted
budgets to keep response times reasonable on Raspberry Pi hardware.

For a Pi Zero 2 W, start with:

```bash
play-poker --bots 5 --simulations 800
```

Increase to 1,200–2,000 if decision time remains acceptable. Raising the number
does not improve the preflop charts; it mainly stabilizes postflop equity.

Bots use a small defense margin around exact pot odds instead of folding every
slightly negative estimate. Cheap calls, heads-up pots, and useful draws receive
more latitude; very weak hands facing expensive bets still fold.

Bluffing remains controlled but is less mechanical. Bots recognize nut-suit and
straight blockers, missed turn draws, scare cards, paired boards, recent checks,
position, and opponent fold tendencies. Each decision also receives bounded
personality-weighted "daring" noise, so strong bluff candidates are sometimes
fired and sometimes checked rather than following a fixed pattern. River
blocker bluffs use larger polarized sizes; draw-heavy semi-bluffs use smaller
sizes. Multiway bluffing remains heavily restricted.

Other inexpensive strategy improvements include protection bets on dynamic
boards, occasional dry-board slowplays, larger thin-value bets against players
who fold too little, low stack-to-pot commitment logic, squeeze raises after
limpers, and slightly wider late-position steals.

Speculative aggression is globally tempered to roughly half strength. This
reduces marginal raises, protection bets, bluff frequency, daring variance, and
default bet sizes while preserving decisive value betting with premium hands.
The aim is to create longer matches without making the bots passive.

The Monte Carlo hot path is optimized for small computers: `treys` card values
are cached, weighted opponent ranges use a small candidate pool instead of
repeated rejection sampling, dealt cards are removed by index, and multiway
pots receive smaller dynamic trial budgets. These changes reduce latency while
retaining action-weighted opponent ranges.

## Graphical multiplayer table

Launch the desktop UI:

```bash
source .venv/bin/activate
python poker_ui.py
```

Or use the installed shortcut:

```bash
poker-table
```

The setup screen lets you choose:

- 1–7 real players
- 0–7 bots
- 2–8 total seats
- starting chips and blind sizes
- Monte Carlo simulations per bot decision

Real players share the computer in pass-and-play mode. At the start of each
human turn, a privacy dialog asks for the computer to be passed to that player.
Only after they click **Reveal my cards** are their hole cards shown. The cards
are hidden again immediately after the action.

The table provides buttons for fold, check/call, bet/raise, and all-in. It
displays stacks, street contributions, community cards, dealer position, pot,
whose turn it is, and a running action log. Bots pause briefly before acting so
their decisions remain readable.

Unlike the original terminal game, the graphical table supports multiplayer
betting and side pots for all-in situations. It remains a local shared-screen
game; it does not connect multiple computers over a network.

## Python usage

```python
from poker_ai import (
    Card, GameStage, GameState, MonteCarloEvaluator, Position, StrategyEngine
)

state = GameState(
    hole_cards=(Card.parse("As"), Card.parse("Kh")),
    community_cards=(
        Card.parse("Qs"), Card.parse("Jh"), Card.parse("2c")
    ),
    pot_size=100,
    amount_to_call=20,
    opponent_actions=(),
    num_opponents=1,
    stage=GameStage.FLOP,
    can_check=False,
    position=Position.LATE,
)

engine = StrategyEngine(
    evaluator=MonteCarloEvaluator(simulations=5_000, seed=42),
    seed=42,
)
decision = engine.decide(state)
print(decision.action.value, decision.amount)
```

## Raspberry Pi considerations

Reduce simulations to roughly 500–2,000 for faster decisions. The evaluator
has no large model or data files, and the strategy layer is ordinary Python.
Keep the random seed only for repeatable tests; omit it in live play.

## Tests

```bash
pytest
```
