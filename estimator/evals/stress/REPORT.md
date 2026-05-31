# CAG Stress Baseline Report

Baseline cuantitativo del sistema CAG conversacional previo a RAG.

## Tabla resumen

| scenario | attachment_kb | P50 latency_ms | P95 latency_ms | total cost USD | exact cache hit | semantic cache hit | mean memory drift |
|---|---:|---:|---:|---:|---:|---:|---:|
| contradiction | 0 | 5000 | 7763 | 0.0164 | 0.00% | 0.00% | 36.96% |
| contradiction | 100 | 5710 | 8594 | 0.0334 | 0.00% | 0.00% | 45.18% |
| contradiction | 20 | 5963 | 15142 | 0.0339 | 0.00% | 0.00% | 36.96% |
| contradiction | 5 | 4820 | 7578 | 0.0202 | 0.00% | 0.00% | 40.76% |
| contradiction | 50 | 5685 | 7360 | 0.0332 | 0.00% | 0.00% | 36.96% |
| growing | 0 | 5191 | 7319 | 0.0140 | 0.00% | 0.00% | 34.69% |
| growing | 100 | 15067 | 22743 | 0.0747 | 0.00% | 0.00% | 47.98% |
| growing | 20 | 13059 | 19443 | 0.0611 | 0.00% | 0.00% | 55.40% |
| growing | 5 | 18791 | 29882 | 0.0577 | 0.00% | 0.00% | 51.42% |
| growing | 50 | 23767 | 41845 | 0.0822 | 0.00% | 0.00% | 54.96% |
| pivot | 0 | 4568 | 8940 | 0.0163 | 0.00% | 0.00% | 23.81% |
| pivot | 100 | 4983 | 9451 | 0.0334 | 0.00% | 0.00% | 23.81% |
| pivot | 20 | 4834 | 10684 | 0.0331 | 0.00% | 0.00% | 23.81% |
| pivot | 5 | 11351 | 15925 | 0.0423 | 0.00% | 0.00% | 23.81% |
| pivot | 50 | 5393 | 15799 | 0.0332 | 0.00% | 0.00% | 23.81% |

## Curva 1 — latency_ms vs tokens_in

### contradiction

| tokens_in | latency_ms |
|---:|---:|
| 2919 | 2498 |
| 3120 | 3077 |
| 3284 | 2183 |
| 3447 | 3219 |
| 3611 | 2395 |
| 3770 | 2695 |
| 4017 | 2517 |
| 4021 | 3800 |
| 4041 | 2892 |
| 4088 | 2888 |
| 4178 | 2699 |
| 4498 | 4263 |
| 4623 | 4404 |
| 4656 | 3974 |
| 4668 | 4994 |
| 4714 | 4999 |
| 4800 | 4771 |
| 4822 | 4728 |
| 4828 | 5059 |
| 4836 | 5456 |
| 4846 | 6634 |
| 4850 | 4763 |
| 4868 | 5685 |
| 4872 | 5219 |
| 4884 | 5176 |
| 4885 | 5701 |
| 4896 | 6930 |
| 4912 | 5281 |
| 4914 | 7578 |
| 4922 | 5346 |
| … | (70 more rows in CSV) |

### growing

| tokens_in | latency_ms |
|---:|---:|
| 2919 | 3186 |
| 3062 | 3260 |
| 3203 | 3170 |
| 3391 | 2418 |
| 3544 | 3909 |
| 3691 | 2446 |
| 4405 | 4460 |
| 4528 | 5199 |
| 4554 | 5123 |
| 4590 | 6669 |
| 4628 | 5191 |
| 4671 | 5967 |
| 4712 | 5369 |
| 4777 | 6242 |
| 4829 | 7188 |
| 4872 | 7046 |
| 4905 | 8065 |
| 5053 | 7319 |
| 6437 | 8294 |
| 6840 | 10463 |
| 6915 | 11266 |
| 6973 | 10741 |
| 7014 | 12521 |
| 7162 | 12454 |
| 7173 | 10808 |
| 7207 | 11290 |
| 7214 | 19438 |
| 7282 | 11667 |
| 7338 | 12732 |
| 7390 | 14263 |
| … | (64 more rows in CSV) |

### pivot

| tokens_in | latency_ms |
|---:|---:|
| 2925 | 2505 |
| 3111 | 2449 |
| 3268 | 2010 |
| 3433 | 2261 |
| 3619 | 2030 |
| 3766 | 2396 |
| 4489 | 3490 |
| 4638 | 4230 |
| 4667 | 4492 |
| 4691 | 3561 |
| 4693 | 4028 |
| 4694 | 4259 |
| 4703 | 3989 |
| 4713 | 4417 |
| 4730 | 4141 |
| 4746 | 12833 |
| 4751 | 5142 |
| 4756 | 4018 |
| 4760 | 6110 |
| 4768 | 4568 |
| 4769 | 8940 |
| 4779 | 4067 |
| 4788 | 4587 |
| 4792 | 5252 |
| 4795 | 4364 |
| 4797 | 6555 |
| 4797 | 4037 |
| 4813 | 5005 |
| 4813 | 3915 |
| 4815 | 4483 |
| … | (70 more rows in CSV) |

## Curva 2 — coste acumulado vs turn_index

### contradiction

| turn_index | cost_usd (turn) | cost_usd (accum) |
|---:|---:|---:|
| 1 | 0.002997 | 0.002997 |
| 2 | 0.001792 | 0.004789 |
| 3 | 0.001817 | 0.006606 |
| 4 | 0.001843 | 0.008449 |
| 5 | 0.001869 | 0.010318 |
| 6 | 0.001140 | 0.011458 |

### growing

| turn_index | cost_usd (turn) | cost_usd (accum) |
|---:|---:|---:|
| 1 | 0.004852 | 0.004852 |
| 2 | 0.003730 | 0.008582 |
| 3 | 0.003310 | 0.011893 |
| 4 | 0.003158 | 0.015051 |
| 5 | 0.004689 | 0.019740 |
| 6 | 0.002768 | 0.022509 |

### pivot

| turn_index | cost_usd (turn) | cost_usd (accum) |
|---:|---:|---:|
| 1 | 0.003263 | 0.003263 |
| 2 | 0.001840 | 0.005103 |
| 3 | 0.002139 | 0.007243 |
| 4 | 0.002197 | 0.009439 |
| 5 | 0.002251 | 0.011690 |
| 6 | 0.001331 | 0.013021 |

## Curva 3 — MemoryDrift recall vs N

### contradiction

| turn_index | mean memory_drift |
|---:|---:|
| 1 | 100.00% |
| 2 | 0.00% |
| 3 | 0.00% |
| 4 | 0.00% |
| 5 | 5.00% |
| 6 | 45.49% |

### growing

| turn_index | mean memory_drift |
|---:|---:|
| 1 | 80.00% |
| 2 | 0.00% |
| 3 | 0.00% |
| 4 | 30.66% |
| 5 | 41.33% |
| 6 | 55.80% |

### pivot

| turn_index | mean memory_drift |
|---:|---:|
| 1 | 100.00% |
| 2 | 0.00% |
| 3 | 0.00% |
| 4 | 33.33% |
| 5 | 50.00% |
| 6 | 19.52% |

## Lectura

A partir del turno N=2, el recall medio del fact-tracker cae por debajo del 60% en este baseline. Eso indica que la ventana deslizante + summary empiezan a perder hechos tempranos antes de que falle el esquema — degradación silenciosa.

La dimensión que más domina la degradación aquí es **latencia**: el turno 6 multiplica el coste del turno 1 por 0.5x (presupuesto 0.05 USD/turno; coste medio turno 1 0.0037 USD, turno 6 0.0017 USD), y la latencia P95 global es 26924 ms (SLA 4000 ms). Con adjuntos ≥50 KB la P95 sube a 31353 ms. Un caso límite que justificaría RAG sería combinar turno ≥2, adjuntos grandes y drift de metadata — el contexto deja de caber en el presupuesto sin que el CI lo detecte.
