# Holographic Memory POC Report

Generated: `2026-05-09T19:10:31.992475+03:00`

Acceptance passed: `True`

## hindsight_fork_maintenance
- baseline: include=2/3, forbidden=1, tokens=89, latency_ms=170.4, sources={'baseline': 4, 'semantic': 4, 'bm25': 4}
- entity_labels: include=2/3, forbidden=1, tokens=89, latency_ms=124.4, sources={'baseline': 4, 'semantic': 4, 'bm25': 4}
- entity_tools: include=2/3, forbidden=1, tokens=89, latency_ms=123.7, sources={'entity_reason': 1, 'baseline': 3, 'semantic': 4, 'bm25': 4}
- entity_labels_entity_tools: include=2/3, forbidden=1, tokens=89, latency_ms=123.0, sources={'entity_reason': 1, 'baseline': 3, 'semantic': 4, 'bm25': 4}
- trust: include=2/3, forbidden=1, tokens=89, latency_ms=122.7, sources={'baseline': 4, 'semantic': 4, 'bm25': 4}
- structural: include=2/3, forbidden=1, tokens=89, latency_ms=126.6, sources={'baseline': 4, 'semantic': 4, 'bm25': 4, 'structural': 4}

## production_incident_memory
- baseline: include=1/4, forbidden=1, tokens=20, latency_ms=114.3, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_labels: include=1/4, forbidden=1, tokens=20, latency_ms=90.1, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_tools: include=4/4, forbidden=0, tokens=32, latency_ms=84.8, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- entity_labels_entity_tools: include=4/4, forbidden=0, tokens=32, latency_ms=85.6, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- trust: include=1/4, forbidden=1, tokens=20, latency_ms=82.0, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- structural: include=1/4, forbidden=1, tokens=20, latency_ms=84.9, sources={'baseline': 1, 'semantic': 1, 'bm25': 1, 'structural': 1}

## home_automation
- baseline: include=1/4, forbidden=0, tokens=26, latency_ms=131.4, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_labels: include=1/4, forbidden=0, tokens=26, latency_ms=100.3, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_tools: include=3/4, forbidden=0, tokens=34, latency_ms=102.4, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- entity_labels_entity_tools: include=3/4, forbidden=0, tokens=34, latency_ms=104.5, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- trust: include=1/4, forbidden=0, tokens=26, latency_ms=101.1, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- structural: include=1/4, forbidden=0, tokens=26, latency_ms=101.3, sources={'baseline': 1, 'semantic': 1, 'bm25': 1, 'structural': 1}

## preference_correction
- baseline: include=3/3, forbidden=1, tokens=40, latency_ms=114.9, sources={'baseline': 2, 'semantic': 2, 'bm25': 2}
- entity_labels: include=3/3, forbidden=1, tokens=40, latency_ms=102.7, sources={'baseline': 2, 'semantic': 2, 'bm25': 2}
- entity_tools: include=3/3, forbidden=1, tokens=40, latency_ms=108.1, sources={'entity_reason': 1, 'baseline': 1, 'semantic': 2, 'bm25': 2}
- entity_labels_entity_tools: include=3/3, forbidden=1, tokens=40, latency_ms=108.0, sources={'entity_reason': 1, 'baseline': 1, 'semantic': 2, 'bm25': 2}
- trust: include=3/3, forbidden=1, tokens=40, latency_ms=105.5, sources={'baseline': 2, 'semantic': 2, 'bm25': 2}
- structural: include=3/3, forbidden=1, tokens=40, latency_ms=104.2, sources={'baseline': 2, 'semantic': 2, 'bm25': 2, 'structural': 2}

## entity_composition
- baseline: include=3/3, forbidden=0, tokens=25, latency_ms=132.6, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_labels: include=3/3, forbidden=0, tokens=25, latency_ms=106.5, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- entity_tools: include=3/3, forbidden=0, tokens=25, latency_ms=103.6, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- entity_labels_entity_tools: include=3/3, forbidden=0, tokens=25, latency_ms=101.4, sources={'entity_reason': 1, 'semantic': 1, 'bm25': 1}
- trust: include=3/3, forbidden=0, tokens=25, latency_ms=99.6, sources={'baseline': 1, 'semantic': 1, 'bm25': 1}
- structural: include=3/3, forbidden=0, tokens=25, latency_ms=102.1, sources={'baseline': 1, 'semantic': 1, 'bm25': 1, 'structural': 1}
