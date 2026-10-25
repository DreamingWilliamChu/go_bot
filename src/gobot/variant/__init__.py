"""Bonus-turn Go: authoritative rules, hidden randomness, observations, search and training.

Layering (lower layers never import higher ones; ``tests/variant/test_isolation.py`` checks the
part that matters for hidden information):

* ``config``, ``rules``, ``scoring``  - settings, board legality/captures/superko, area scoring
* ``state``                           - public game state and its transitions (no hidden data)
* ``randomness``, ``env``             - hidden bonus schedule and the environment that owns it
* ``observation``                     - what players, search and networks may see
* ``serialization``, ``store``        - versioned game records on disk
* ``search``, ``models``, ``training`` - stochastic search, evaluators, self-play training

See docs/BONUS_GO.md for the rules and docs/VARIANT_ARCHITECTURE.md for the design.
"""
