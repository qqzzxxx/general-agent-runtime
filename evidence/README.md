# evidence/

Pinned regression baselines only.

The files under this directory are frozen snapshots of earlier product prompts and recorded
comparison baselines. The offline regression suite pins the current
product contracts against them so that accidental prompt/contract drift
is caught by tests. They are test fixtures, not runtime data, and the
Runtime itself never writes here.
