"""One colour and one caption per map glyph, for everything that draws one.

WHY THIS EXISTS. Eight files draw pictures of the same map - the window's
progress bar, the taskbar strip, and six page generators - and until this they
each carried their own hex codes: 254 literal colours over 124 distinct values.
They had drifted, and the drift was on the one state a reader would most want
to get right. Measured 4 Sep:

    glyph   the window          the generators
    +       #4f9d69             #4f9d69
    -       #cf4b3a             #cf4b3a
    W       #7a1c14             #7a1c14  (make_crazy_rich only)
    x       the track's grey    #e0a72e  AMBER
    F       #e0a72e  AMBER      `!` at #9b59d0  (make_salvage_map)

So the same amber meant "nobody asked" on a page and "the drive answered and
the answer was false" in the window, and fabrication did not even agree on
which character it is. Backlog A9.

WHAT WAS DECIDED, AND WHY IT IS NOT A COMPROMISE.

`x` skipped is NOT amber, and not a colour of its own either. Nothing in a
skipped stretch was measured, so it is in exactly the state the unread end of
the bar is in - and giving it a colour of its own said "here is a finding"
about ground nobody asked a question of. It gets the same quiet blue-grey as
`?`, which is what docs/make_puss_story.py and docs/make_since_the_change.py -
the two most recent generators - already drew it in.

Amber therefore belongs to `F` alone: bytes came back, the map says they were
read, and they are not the disc's. That is a finding, and it is the only
non-fatal one.

`UNMEASURED` carries the rest of the meaning across the two media. A live
progress bar HAS a track, so there the honest drawing of "nobody measured
this" is to leave the track showing; an SVG strip fills every cell and has no
track to leave, so it draws the blue-grey. Same statement, two renderings -
which is why `map_colour()` takes the ground it is drawing on rather than
returning None and leaving each caller to invent a rule.

`F` is the glyph, because `MAP_FABRICATED` is the engine's constant and the
engine is what emits the characters. `!` was make_salvage_map's own invention.

KEYED ON THE LITERAL CHARACTERS, and this module imports nothing. Both are
deliberate: discripper_gui.py must never import the engine (a second copy of a
35,000-line module, with its own globals, including the ones the strip reads),
and a leaf with no imports can be pulled in at module scope by the window, by
the generators and by the engine alike. test_the_map_colours_are_one_table
ties the keys here to the engine's MAP_* constants, so a new state cannot be
added there and forgotten here.
"""

# glyph -> (colour, short label, the caption a legend prints)
MAP_COLOUR = {
    "+": ("#4f9d69", "read",
          "READ - the disc gave it up"),
    "-": ("#cf4b3a", "dead",
          "UNREADABLE - the drive was asked and refused"),
    # DEEPER THAN A REFUSAL, because it is worse than one and it is the only
    # thing on a bar a person can act on: a range that took the drive off the
    # bus does not just lack data, touching it again costs a replug.
    "W": ("#7a1c14", "wedged",
          "WEDGED - reading this took the drive off the bus"),
    "F": ("#e0a72e", "fabricated",
          "FABRICATED - bytes came back, but not the disc's"),
    # QUIET, AND NOT WARM. This is the colour docs/make_puss_story.py and
    # docs/make_since_the_change.py - the two most recent generators - already
    # drew a skipped stretch in, while the three older ones used amber for it.
    # Picking theirs means those two pages do not change at all, which is a
    # useful check that this is the right way round rather than a coin toss.
    "x": ("#5878a8", "skipped",
          "SKIPPED - stepped past, never asked"),
    # DARKER STILL, because it is the emptiest of the states: not skipped over,
    # simply not reached. The same two generators call it C_TODO and draw it in
    # this, and on a bar it is what the unfilled track looks like anyway.
    "?": ("#3f4d5c", "not asked",
          "NOT ASKED YET - the run has not reached it"),
    # NOT A STATE OF THE DISC, and only the page generators have it: ground
    # outside the run entirely - the extras beside a feature-only rip.
    ".": ("#2a2f37", "outside",
          "outside this run - the extras"),
}

# The glyphs that are not a finding. See the module docstring: a renderer with
# a track of its own draws these as the track, because nothing in them was
# measured and a colour would claim otherwise.
#
# `.` is NOT one of them, though nothing in it was measured either: it says
# "this is outside the question" rather than "this part of the question is
# unanswered", and only a page that draws a window onto a disc has any use for
# the difference. No bar emits it.
UNMEASURED = ("x", "?")

# What a legend lists, in the order it lists them: worst first, and the ones
# that mean "nobody asked" last. `.` is left out - it is a property of a page's
# window onto the disc, not a state a reader has to learn.
LEGEND_ORDER = ("+", "-", "W", "F", "x")


# "no ground was given", which is NOT the same as a ground of None: a bar
# whose track shows through asks for exactly None, and a default of None could
# not tell the two apart. Cost one round of this returning the blue-grey to a
# progress bar that had asked for its own track.
_NO_GROUND = object()


def map_colour(glyph, ground=_NO_GROUND):
    """The colour for one glyph, or `ground` for the ones nobody measured.

    `ground` is what the caller is drawing on - a bar's track, a page's
    background - and passing it at all is what says "I have somewhere for the
    unmeasured states to disappear into". Pass `None` and they come back as
    None, which is a progress bar leaving its track showing; leave it out
    entirely and every state gets a colour, which is what a strip that must
    fill every cell needs.
    """
    key = str(glyph or "")
    given = ground is not _NO_GROUND
    if given and key in UNMEASURED:
        return ground
    entry = MAP_COLOUR.get(key)
    if entry:
        return entry[0]
    return ground if given else None


def map_label(glyph):
    """The two-word form, for a legend beside a bar."""
    entry = MAP_COLOUR.get(str(glyph or ""))
    return entry[1] if entry else ""


def map_caption(glyph):
    """The sentence form, for a legend under a page's strip."""
    entry = MAP_COLOUR.get(str(glyph or ""))
    return entry[2] if entry else ""


def MAP_LEGEND(ground=_NO_GROUND, short=True):
    """[(label, colour)] in legend order, ready to draw.

    A function rather than a constant because the answer depends on what is
    being drawn on - see map_colour - and a constant would have to pick one
    medium and be wrong in the other.
    """
    return [(map_label(g) if short else map_caption(g),
             map_colour(g, ground)) for g in LEGEND_ORDER]
