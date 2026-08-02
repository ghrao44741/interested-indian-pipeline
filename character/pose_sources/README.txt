Raw generation output. NOT runtime assets.

These are the pre-removal images returned by the model, kept for provenance and
re-processing. They are opaque and unapproved.

Runtime must never discover assets by globbing. The compositor and router resolve
only exact paths from pose_library.registry in character/character_spec.json, via
pose_registry.resolve(). A stray raw or in-progress file in the poses directory
must not be pickable.
