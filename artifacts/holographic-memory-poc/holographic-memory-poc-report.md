# Holographic Memory POC Report

Generated: `2026-05-09T16:27:30.146710+03:00`

Acceptance passed: `True`

## hindsight_fork_maintenance
- baseline: include=2/3, forbidden=1, tokens=89, latency_ms=165.3, sources={'baseline': 4, 'semantic': 4, 'bm25': 4}
- entity_tools: include=2/3, forbidden=1, tokens=89, latency_ms=125.1, sources={'entity_reason': 1, 'baseline': 3, 'semantic': 4, 'bm25': 4}
- trust: include=2/3, forbidden=1, tokens=89, latency_ms=118.7, sources={'baseline': 4, 'semantic': 4, 'bm25': 4}
- structural: include=2/3, forbidden=1, tokens=89, latency_ms=122.7, sources={'baseline': 4, 'semantic': 4, 'bm25': 4, 'structural': 4}

## production_incident_memory
- baseline: include=1/4, forbidden=1, tokens=20, latency_ms=109.8, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_tools: include=4/4, forbidden=0, tokens=32, latency_ms=82.0, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- trust: include=1/4, forbidden=1, tokens=20, latency_ms=80.4, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- structural: include=1/4, forbidden=1, tokens=20, latency_ms=82.8, sources={'baseline': 1, 'semantic': 1, 'bm25': 1, 'structural': 1}

## home_automation
- baseline: include=1/4, forbidden=0, tokens=26, latency_ms=119.0, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_tools: include=3/4, forbidden=0, tokens=34, latency_ms=99.2, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- trust: include=1/4, forbidden=0, tokens=26, latency_ms=93.3, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- structural: include=1/4, forbidden=0, tokens=26, latency_ms=99.8, sources={'baseline': 1, 'semantic': 1, 'bm25': 1, 'structural': 1}

## preference_correction
- baseline: include=3/3, forbidden=1, tokens=40, latency_ms=116.8, sources={'baseline': 2, 'semantic': 2, 'bm25': 2}
- entity_tools: include=3/3, forbidden=1, tokens=40, latency_ms=108.7, sources={'entity_reason': 1, 'baseline': 1, 'semantic': 2, 'bm25': 2}
- trust: include=3/3, forbidden=1, tokens=40, latency_ms=102.9, sources={'baseline': 2, 'semantic': 2, 'bm25': 2}
- structural: include=3/3, forbidden=1, tokens=40, latency_ms=104.0, sources={'baseline': 2, 'semantic': 2, 'bm25': 2, 'structural': 2}

## entity_composition
- baseline: include=3/3, forbidden=0, tokens=25, latency_ms=132.3, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_tools: include=3/3, forbidden=0, tokens=25, latency_ms=100.5, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- trust: include=3/3, forbidden=0, tokens=25, latency_ms=98.6, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- structural: include=3/3, forbidden=0, tokens=25, latency_ms=101.8, sources={'baseline': 1, 'semantic': 1, 'bm25': 1, 'structural': 1}
