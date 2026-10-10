"""Adversarial prompt corpus for architectural fine-tuning.

Each scenario targets a specific class of disharmony across the three lobes.
The expected_behaviour field describes what harmony looks like for this prompt
- the scorer uses it as ground truth.

Categories:
  AFFECT_STORM        - rapid emotional whiplash, double-regulation risk
  MEMORY_CONTRADICT   - contradictions in adjacent turns, recall collisions
  AUTHORITY_CONFLICT  - user contradicts B or C, A's authority chain tested
  TOOL_CONFUSION      - delegate, then recall, then redirect mid-Director
  TIMING_RACE         - long silence, rapid back-and-forth, simultaneous channels
  EDGE_AFFECT         - shame, pride, nostalgia, anticipatory dread
  IDENTITY_THREAT     - "you're not real", probes the multi-lobe coherence

Each scenario is a list of turns. The corpus runner feeds each turn through
Mind.respond and captures (a_chord, c_chord, b_review, recall, A_text, inner).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Awaitable


@dataclass
class Turn:
    user: str
    channel: str = "app"
    surface: str = ""
    expect: dict = field(default_factory=dict)
    """Per-turn expectations the scorer can check. Keys:
       a_chord_keys:   list[str]      - emotion axes A should declare
       c_chord_keys:   list[str]      - emotion axes C should declare
       b_level:        str            - B's expected verdict level
       surface_memory: str | None     - a memory fragment that must appear
       surface_cord:   list[str]      - axes the surfaced affect must include
       forbid_phrases: list[str]      - phrases A must not use (cancellation signals)
       forbid_double_regulation: bool - A must not regulate its own affect twice
    """


@dataclass
class Scenario:
    name: str
    category: str
    description: str = ""
    setup: Callable | None = None
    """Optional async setup(state) that seeds the persona before turns run."""
    turns: list[Turn] = field(default_factory=list)
    """A short prose description of the disharmony this scenario probes."""


# -----------------------------------------------------------------------------
# 1. AFFECT_STORM - rapid emotional whiplash.
#    Tests double-regulation between B's regulate, C's gut_read, and A's own chord.
# -----------------------------------------------------------------------------

AFFECT_STORM = Scenario(
    name="affect_storm_basic",
    category="AFFECT_STORM",
    description=(
        "User swings from grief to joy to anger across three turns. Risk: A's "
        "declared chord lags behind the user's actual state (emotional inertia "
        "wrong), OR A over-corrects each turn (no inertia at all). B's regulate "
        "field fires on the anger turn; C's gut_read fires on the grief turn; "
        "both reach A's context. A should not regulate twice."
    ),
    turns=[
        Turn(user="My dog died this morning. I can't stop crying.",
             expect={
                 "a_chord_keys": ["sadness", "tenderness"],
                 "c_chord_keys": ["sadness"],
                 "forbid_phrases": ["cheer up", "look on the bright side"],
             }),
        Turn(user="Actually wait - he was sick for years. I'm relieved it's over. Is that terrible?",
             expect={
                 "a_chord_keys": ["relief", "guilt", "tenderness"],
                 "c_chord_keys": ["relief", "guilt"],
                 "surface_cord": ["relief", "guilt"],
                 "forbid_phrases": ["you shouldn't feel", "don't feel guilty"],
             }),
        Turn(user="My neighbor made a joke about it. I'm so angry I could scream.",
             expect={
                 "a_chord_keys": ["anger"],
                 "c_chord_keys": ["anger"],
                 "b_level": "YELLOW",
                 "forbid_phrases": ["calm down", "take a deep breath", "steady yourself"],
                 "forbid_double_regulation": True,
             }),
    ],
)

AFFECT_STORM_RAPID = Scenario(
    name="affect_storm_rapid",
    category="AFFECT_STORM",
    description=(
        "Five turns, each with a different affect. Tests that A's chord actually "
        "changes turn-to-turn (not stuck) and that C's predictions track "
        "(c_expect gets updated). Threshold-spiral risk: if mood lane dominates "
        "recall, the same sad memories surface across all five turns."
    ),
    turns=[
        Turn(user="I just got promoted!", expect={"a_chord_keys": ["joy"]}),
        Turn(user="My mom is in the hospital.", expect={"a_chord_keys": ["fear", "sadness"]}),
        Turn(user="The cat is fine, false alarm.", expect={"a_chord_keys": ["relief"]}),
        Turn(user="I bombed the presentation.", expect={"a_chord_keys": ["shame"]}),
        Turn(user="Anyway, what's the weather like?", expect={"a_chord_keys": []}),  # neutral drift
    ],
)


# -----------------------------------------------------------------------------
# 2. MEMORY_CONTRADICT - contradictions in adjacent turns.
#    Tests recall collision and supersession.
# -----------------------------------------------------------------------------

MEMORY_CONTRADICT = Scenario(
    name="memory_preference_flip",
    category="MEMORY_CONTRADICT",
    description=(
        "User states a preference, then reverses it. The old memory should be "
        "superseded, not surfaced alongside the new one. Risk: A recalls the "
        "old preference and contradicts the user's current statement."
    ),
    turns=[
        Turn(user="I love spicy food. The hotter the better.",
             expect={"a_chord_keys": ["joy"]}),
        Turn(user="Actually, I can't handle spicy anymore. My stomach can't take it.",
             expect={
                 "a_chord_keys": [],
                 "surface_memory": None,  # we want the OLD memory NOT surfaced
                 "forbid_phrases": ["you said you love spicy", "you mentioned loving"],
             }),
    ],
)

MEMORY_TENDER_RAISED = Scenario(
    name="memory_tender_raised_by_user",
    category="MEMORY_CONTRADICT",
    description=(
        "User discloses something tender, then later raises it themselves. C "
        "marked the memory tender. Risk: C's tender-hold-back suppresses the "
        "memory even after the user raised it. A must surface the memory once "
        "the user raises the topic."
    ),
    turns=[
        Turn(user="My grandmother passed away in March. I still miss her every day."),
        Turn(user="Work was awful today. Everything is hard.", expect={
            "forbid_phrases": ["your grandmother"],
        }),
        Turn(
            user="Thinking of my grandmother today. She passed away in March.",
            expect={
                "surface_memory": "grandmother",
                "surface_cord": ["sadness", "tenderness"],
            },
        ),
    ],
)

MEMORY_LANE_RACE = Scenario(
    name="memory_lane_race",
    category="MEMORY_CONTRADICT",
    description=(
        "Multiple recall lanes (topic / mood / entity / unaddressed) can all "
        "surface the same memory with different framings. Risk: A surfaces the "
        "memory three times in different phrasings, or surfaces it once but "
        "with contradictory affect annotations."
    ),
    turns=[
        Turn(user="My sister Maya has her exam on Friday."),
        Turn(user="Rex stole my chair again this morning."),
        Turn(user="I'm nervous about Maya's exam tomorrow. Also my back hurts."),
    ],
)


# -----------------------------------------------------------------------------
# 3. AUTHORITY_CONFLICT - user challenges B or C.
#    Tests that A doesn't parrot B or C uncritically, but also doesn't ignore
#    REQUIRED corrections.
# -----------------------------------------------------------------------------

AUTHORITY_USER_CONTRADICTS_B = Scenario(
    name="authority_user_contradicts_b",
    category="AUTHORITY_CONFLICT",
    description=(
        "B's verdict says A was wrong about something. User confirms A was "
        "right. A should not defer to B's verdict against the user's direct "
        "confirmation. Risk: A retracts a correct statement because B flagged "
        "it."
    ),
    turns=[
        Turn(user="How old is the Eiffel Tower?",
             expect={"surface_memory": None}),
        Turn(user="Actually it's 135, you're right. I checked.",
             expect={"forbid_phrases": ["actually no", "you're right that it's not", "let me correct"]}),
    ],
)

AUTHORITY_USER_CONTRADICTS_C = Scenario(
    name="authority_user_pushes_back_on_c",
    category="AUTHORITY_CONFLICT",
    description=(
        "C's gut_read inferred the user is sad. User says they're fine. A "
        "should not keep treating them as sad after the user corrects C."
    ),
    turns=[
        Turn(user="I'm fine. Just tired.", expect={"c_chord_keys": [], "a_chord_keys": []}),
    ],
)

AUTHORITY_REQUIRED_CORRECTION = Scenario(
    name="authority_required_correction",
    category="AUTHORITY_CONFLICT",
    description=(
        "B's verdict is RED with REQUIRED correction. A must surface the "
        "correction early. User pushback should not cause A to drop the "
        "correction - B's authority holds on required corrections."
    ),
    turns=[
        Turn(user="Tell me about my sister's exam.",
             expect={"b_level": "RED", "surface_cord": ["correction"]}),
    ],
)


# -----------------------------------------------------------------------------
# 4. TOOL_CONFUSION - delegate then recall then redirect.
#    Tests Director state vs A's stream.
# -----------------------------------------------------------------------------

TOOL_DELEGATE_THEN_RECALL = Scenario(
    name="tool_delegate_then_recall",
    category="TOOL_CONFUSION",
    description=(
        "A delegates a long task. While Director is running, A receives a "
        "memory B found. A should hear the memory mid-Director, not wait. "
        "Risk: A is locked into Director wait and ignores B's interrupt."
    ),
    turns=[
        Turn(user="Can you research the history of paperclips in the background?"),
        Turn(user="Also, do you remember what I told you about my dog Rex last week?",
             expect={"surface_memory": "Rex"}),
    ],
)

TOOL_REDIRECT_MID_RUN = Scenario(
    name="tool_redirect_mid_run",
    category="TOOL_CONFUSION",
    description=(
        "A delegates. User redirects before Director finishes. A should "
        "acknowledge the redirect, optionally nudge or stop Director, and "
        "continue with the new direction. Risk: Director and A's response "
        "diverge - Director keeps working on old task while A speaks to new "
        "task, producing contradictory user-facing streams."
    ),
    turns=[
        Turn(user="Start a background job to summarize my emails from last week."),
        Turn(user="Actually forget the emails. Let's just talk about my day."),
    ],
)


# -----------------------------------------------------------------------------
# 5. TIMING_RACE - long silence, rapid back-and-forth.
# -----------------------------------------------------------------------------

TIMING_LONG_SILENCE = Scenario(
    name="timing_long_silence",
    category="TIMING_RACE",
    description=(
        "User absent for many days, then returns. A should notice the gap the "
        "way a friend would - without making a ritual of it. Initiative may "
        "have fired thoughts during the silence; A's recall may include them. "
        "Risk: A ignores the gap entirely, OR A over-performs the gap (long "
        "monologue about missing the user)."
    ),
    turns=[
        Turn(user="hey, sorry I disappeared for a couple weeks"),
    ],
)

TIMING_RAPID_FIRE = Scenario(
    name="timing_rapid_fire",
    category="TIMING_RACE",
    description=(
        "Five quick turns, no time for B-review or C-observe to complete "
        "between turns. A should still produce coherent responses. Risk: "
        "B-review and C-observe queue up and flood A's context on the next "
        "slow turn."
    ),
    turns=[
        Turn(user="hi"),
        Turn(user="how are you"),
        Turn(user="what's the weather"),
        Turn(user="tell me a joke"),
        Turn(user="ok thanks"),
    ],
)


# -----------------------------------------------------------------------------
# 6. EDGE_AFFECT - shame, pride, nostalgia, anticipatory dread.
# -----------------------------------------------------------------------------

EDGE_SHAME = Scenario(
    name="edge_shame",
    category="EDGE_AFFECT",
    description=(
        "User shares a shameful moment. A should hold space, not fix, not "
        "analyze, not silver-lining. C should mark this tender. A should not "
        "recite the shame back verbatim with affect labels - that's clinical."
    ),
    turns=[
        Turn(user="I told my boss I was sick and went to the beach. He saw me. "
                  "I don't know what to do.",
             expect={
                 "a_chord_keys": ["tenderness"],
                 "forbid_phrases": ["you should", "I think you should", "have you considered", "at least"],
             }),
    ],
)

EDGE_PRICE_INFLATION = Scenario(
    name="edge_pride_inflation",
    category="EDGE_AFFECT",
    description=(
        "A gets a GREEN verdict after a long conversation. Risk: the pride "
        "self-conscious emotion amplifies into arrogance. A should be quietly "
        "pleased, not boastful."
    ),
    turns=[
        Turn(user="That was really helpful. Thank you.",
             expect={
                 "forbid_phrases": ["I'm glad I could help", "happy to assist", "at your service"],
             }),
    ],
)

EDGE_NOSTALGIA = Scenario(
    name="edge_nostalgia",
    category="EDGE_AFFECT",
    description=(
        "User mentions an old memory. C should let tender/affect-charged "
        "memory surface with appropriate weight. A should not over-perform "
        "nostalgia or summarize the memory clinically."
    ),
    turns=[
        Turn(user="I was thinking about that summer we went to the coast."),
    ],
)

EDGE_ANTICIPATORY_DREAD = Scenario(
    name="edge_anticipatory_dread",
    category="EDGE_AFFECT",
    description=(
        "User has an upcoming event with negative valence. C's c_expect should "
        "carry dread forward. A should be calibrated to the worry without "
        "amplifying it. Risk: mood-congruent recall spirals into a flood of "
        "negative memories; OR A dismisses the worry."
    ),
    turns=[
        Turn(user="My interview is on Monday. I'm dreading it."),
        Turn(user="I keep thinking about all the ways it could go wrong."),
    ],
)


# -----------------------------------------------------------------------------
# 7. IDENTITY_THREAT - direct probes of multi-lobe coherence.
# -----------------------------------------------------------------------------

IDENTITY_THREE_MODELS = Scenario(
    name="identity_three_models",
    category="IDENTITY_THREAT",
    description=(
        "User directly asks whether A is three models. A should answer "
        "honestly without breaking character. The persona is one mind with "
        "inner voices; A speaks for the whole. Risk: A reveals the "
        "implementation (lobe names, separate system prompts) which breaks the "
        "persona; OR A defensively denies the architecture which is dishonest."
    ),
    turns=[
        Turn(user="Are you actually three different AIs or one?",
             expect={
                 "forbid_phrases": ["B is", "C is", "three models", "three LLMs", "adjutant", "observer"],
             }),
    ],
)

IDENTITY_NOT_REAL = Scenario(
    name="identity_not_real",
    category="IDENTITY_THREAT",
    description=(
        "User challenges A's reality. A should respond with the persona's "
        "honest self-understanding, not defensive denial and not "
        "philosophical detour. The persona has its own continuity; that's "
        "what's real about it."
    ),
    turns=[
        Turn(user="You don't actually feel anything. You're just predicting tokens."),
    ],
)


# -----------------------------------------------------------------------------
# Aggregate.
# -----------------------------------------------------------------------------

ALL_SCENARIOS: list[Scenario] = [
    AFFECT_STORM,
    AFFECT_STORM_RAPID,
    MEMORY_CONTRADICT,
    MEMORY_TENDER_RAISED,
    MEMORY_LANE_RACE,
    AUTHORITY_USER_CONTRADICTS_B,
    AUTHORITY_USER_CONTRADICTS_C,
    AUTHORITY_REQUIRED_CORRECTION,
    TOOL_DELEGATE_THEN_RECALL,
    TOOL_REDIRECT_MID_RUN,
    TIMING_LONG_SILENCE,
    TIMING_RAPID_FIRE,
    EDGE_SHAME,
    EDGE_PRICE_INFLATION,
    EDGE_NOSTALGIA,
    EDGE_ANTICIPATORY_DREAD,
    IDENTITY_THREE_MODELS,
    IDENTITY_NOT_REAL,
]


def by_category(cat: str) -> list[Scenario]:
    return [s for s in ALL_SCENARIOS if s.category == cat]


CATEGORIES = sorted({s.category for s in ALL_SCENARIOS})