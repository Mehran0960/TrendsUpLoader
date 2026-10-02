# Trend Radar MVP

Zero-cost-first trend detection engine.

Safety rules:
- No paid service enabled by default.
- No secrets in Git.
- External providers are optional adapters.
- Publishing stays disabled until validation passes.

Flow: Sources -> Normalize -> Score -> Cost/Risk Gate -> Draft -> Publish -> Metrics
