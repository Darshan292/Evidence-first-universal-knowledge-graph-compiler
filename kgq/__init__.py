"""Query side of the compiler: interpretation, retrieval, grounded answering.

`kgc/` compiles source into deterministic claims with byte-exact evidence.
`kgq/` answers questions about it. The split is the trust boundary: nothing here
may write a structural claim, mint an evidence id, or invent a locator.
"""
DEMO_VERSION = "0.1.0"
