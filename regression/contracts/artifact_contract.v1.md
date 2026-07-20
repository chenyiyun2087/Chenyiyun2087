# Golden Regression Artifact Contract v1.0

## Baseline

```json
{
  "schema_version": "1.0",
  "baseline_id": "<component>.vN",
  "metadata": {
    "component": "<stable component name>",
    "fixture_id": "<immutable fixture id>"
  },
  "expected": {
    "invariants": {"<exact key>": "<exact value>"},
    "metrics": {"<numeric key>": 0.0},
    "selection": {
      "symbols": ["000001.SZ"],
      "weights": {"000001.SZ": 0.10}
    },
    "artifacts": {
      "input": {"row_count": 10, "sha256": "<digest>"}
    }
  },
  "tolerances": {
    "metrics.<name>": {"absolute": 0.0, "relative": 0.0},
    "selection.symbols": {"max_symbol_changes": 0, "order_sensitive": true},
    "selection.weights.*": {"absolute": 0.0}
  }
}
```

## Actual artifact

```json
{
  "schema_version": "1.0",
  "baseline_id": "<same baseline id>",
  "metadata": {
    "component": "<same component>",
    "fixture_id": "<same fixture>"
  },
  "result": {
    "invariants": {"<exact key>": "<actual value>"},
    "metrics": {"<numeric key>": 0.0},
    "selection": {"symbols": [], "weights": {}},
    "artifacts": {"input": {"row_count": 10, "sha256": "<digest>"}}
  }
}
```

## Comparison semantics

- Every key appearing under `expected.invariants` and `expected.artifacts` is compared recursively with exact equality.
- Every key under `expected.metrics` must be finite numeric and is compared using the stricter of the declared absolute/relative allowance.
- `selection.symbols` is order-sensitive unless declared otherwise.
- The comparator never grants an implicit tolerance. Missing tolerances are zero.
- The actual artifact may contain extra diagnostic fields; those fields do not change pass/fail status.
