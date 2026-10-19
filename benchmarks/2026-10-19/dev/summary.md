# Entity linking compared, 2026-10-19

126 documents, 351 gold mentions, languages en, it, fr, de, es, pt. These are development sets, not held-out data.

| System | Precision | Recall | F1 | Correct | Wrong | No answer |
| --- | --- | --- | --- | --- | --- | --- |
| resolve() (API) | 93.3 | 91.5 | 92.4 | 321 | 23 | 7 |
| resolve() (open demo) | 93.3 | 91.5 | 92.4 | 321 | 23 | 7 |
| resolve() (API, own mentions) | 95.1 | 88.9 | 91.9 | 312 | 16 | 23 |
| Wikidata search, first hit | 82.5 | 82.1 | 82.3 | 288 | 61 | 2 |
| Entity-Fishing | 90.9 | 71.5 | 80.1 | 251 | 25 | 75 |
| Dandelion | 95.4 | 88.9 | 92.0 | 312 | 15 | 24 |
| Google Cloud Natural Language | 96.1 | 83.8 | 89.5 | 294 | 12 | 45 |

F1 by language:

| System | English | Italian | French | German | Spanish | Portuguese |
| --- | --- | --- | --- | --- | --- | --- |
| resolve() (API) | 93.5 | 88.6 | 91.4 | 94.4 | 90.5 | 93.3 |
| resolve() (open demo) | 93.5 | 88.6 | 91.4 | 94.4 | 90.5 | 93.3 |
| resolve() (API, own mentions) | 94.9 | 90.7 | 85.3 | 95.5 | 88.9 | 91.2 |
| Wikidata search, first hit | 81.4 | 81.8 | 70.3 | 89.9 | 83.3 | 84.3 |
| Entity-Fishing | 85.4 | 54.5 | 40.0 | 92.9 | 82.2 | 89.0 |
| Dandelion | 92.6 | 89.7 | 93.0 | 90.9 | 95.1 | 91.2 |
| Google Cloud Natural Language | 87.6 | 92.9 | 86.6 | 94.1 | 85.7 | 90.6 |

resolve() (API) by decision path:

| Path | Answered | Correct | Accuracy | Declined |
| --- | --- | --- | --- | --- |
| fast | 123 | 122 | 99.2 | 0 |
| reranked | 221 | 199 | 90.0 | 7 |

resolve() (open demo) by decision path:

| Path | Answered | Correct | Accuracy | Declined |
| --- | --- | --- | --- | --- |
| fast | 123 | 122 | 99.2 | 0 |
| reranked | 221 | 199 | 90.0 | 7 |

resolve() (API, own mentions) by decision path:

| Path | Answered | Correct | Accuracy | Declined |
| --- | --- | --- | --- | --- |
| fast | 118 | 117 | 99.2 | 1 |
| none | 0 | 0 |  | 21 |
| reranked | 210 | 195 | 92.9 | 1 |

Head to head with resolve() (API), per mention:

| Against | Only resolve() right | Only it right, resolve() wrong | Only it right, resolve() declined | Neither right | Its wrong answers | Of those, resolve() right |
| --- | --- | --- | --- | --- | --- | --- |
| Wikidata search, first hit | 41 | 6 | 2 | 22 | 61 | 39 |
| Entity-Fishing | 83 | 11 | 2 | 17 | 25 | 19 |
| Dandelion | 30 | 18 | 3 | 9 | 15 | 11 |
| Google Cloud Natural Language | 47 | 14 | 6 | 10 | 12 | 9 |
