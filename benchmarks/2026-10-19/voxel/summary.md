# Entity linking compared, 2026-10-19

75 documents, 1005 gold mentions, languages en, it, fr, de, es. VoxEL (ISWC 2018, CC BY 4.0): news in five languages that none of these systems was tuned on.

| System | Precision | Recall | F1 | Correct | Wrong | No answer |
| --- | --- | --- | --- | --- | --- | --- |
| resolve() (API) | 96.0 | 88.0 | 91.8 | 884 | 37 | 84 |
| resolve() (API, own mentions) | 96.8 | 78.9 | 87.0 | 793 | 26 | 186 |
| Wikidata search, first hit | 76.0 | 74.9 | 75.5 | 753 | 238 | 14 |
| Entity-Fishing | 88.8 | 69.2 | 77.7 | 695 | 88 | 222 |
| Dandelion | 95.1 | 78.0 | 85.7 | 784 | 40 | 181 |
| Google Cloud Natural Language | 97.6 | 83.4 | 89.9 | 838 | 21 | 146 |

F1 by language:

| System | English | Italian | French | German | Spanish |
| --- | --- | --- | --- | --- | --- |
| resolve() (API) | 93.1 | 92.6 | 91.1 | 87.0 | 94.9 |
| resolve() (API, own mentions) | 87.7 | 87.3 | 86.6 | 82.5 | 90.4 |
| Wikidata search, first hit | 60.7 | 84.8 | 70.3 | 76.3 | 85.2 |
| Entity-Fishing | 80.7 | 57.1 | 69.0 | 87.9 | 89.0 |
| Dandelion | 81.5 | 88.9 | 86.5 | 81.0 | 90.2 |
| Google Cloud Natural Language | 92.7 | 85.8 | 88.2 | 90.1 | 92.5 |

resolve() (API) by decision path:

| Path | Answered | Correct | Accuracy | Declined |
| --- | --- | --- | --- | --- |
| fast | 222 | 218 | 98.2 | 0 |
| reranked | 699 | 666 | 95.3 | 84 |

resolve() (API, own mentions) by decision path:

| Path | Answered | Correct | Accuracy | Declined |
| --- | --- | --- | --- | --- |
| fast | 217 | 214 | 98.6 | 0 |
| none | 0 | 0 |  | 184 |
| reranked | 602 | 579 | 96.2 | 2 |

Head to head with resolve() (API), per mention:

| Against | Only resolve() right | Only it right, resolve() wrong | Only it right, resolve() declined | Neither right | Its wrong answers | Of those, resolve() right |
| --- | --- | --- | --- | --- | --- | --- |
| Wikidata search, first hit | 159 | 9 | 19 | 93 | 238 | 159 |
| Entity-Fishing | 239 | 18 | 32 | 71 | 88 | 66 |
| Dandelion | 151 | 13 | 38 | 70 | 40 | 21 |
| Google Cloud Natural Language | 107 | 18 | 43 | 60 | 21 | 9 |
