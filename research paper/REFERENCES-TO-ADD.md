# References to Add

The eleven citations requested by `Revision_Report (1).docx` rows 3–9 and 15,
ready to paste. IEEE format first, BibTeX at the end.

**Verification status is marked on every entry and is not decoration.** Nine
were checked against the Crossref REST API or the arXiv API on **2026-10-13**;
the DOI or arXiv identifier is given so you can re-check any of them in one
request. Two could not be verified and say so. Nothing here was written from
memory and then dressed as a record.

How to re-check any Crossref entry:

```bash
curl -s "https://api.crossref.org/works/<DOI>" | python -m json.tool
```

---

## Verified — metric and method sources (row 3, methodological part)

**NDCG** — ✅ verified, Crossref `10.1145/582415.582418`

> K. Järvelin and J. Kekäläinen, "Cumulated gain-based evaluation of IR
> techniques," *ACM Transactions on Information Systems*, vol. 20, no. 4,
> pp. 422–446, 2002, doi: 10.1145/582415.582418.

**Kendall's τ-b** — ✅ verified, Crossref `10.1093/biomet/33.3.239`

> M. G. Kendall, "The treatment of ties in ranking problems," *Biometrika*,
> vol. 33, no. 3, pp. 239–251, 1945, doi: 10.1093/biomet/33.3.239.

This is the right citation specifically for **τ-b**: it is the paper that
introduces the tie correction, which matters here because 97 of 100 candidates
in the cohort share an LLM score. Citing Kendall's 1938 "A new measure of rank
correlation" instead would cite τ-a and miss the point.

**BM25** — ✅ verified, Crossref `10.1561/1500000019`

> S. Robertson and H. Zaragoza, "The Probabilistic Relevance Framework: BM25 and
> Beyond," *Foundations and Trends in Information Retrieval*, vol. 4, no. 1–2,
> pp. 1–174, 2009, doi: 10.1561/1500000019.

Cite this rather than a TREC proceedings paper: it is the source that states
BM25 with the *k₁* and *b* parameterisation the implementation uses
(`k₁ = 1.5`, `b = 0.75`).

**TF-IDF / inverse document frequency** — ✅ verified, Crossref `10.1108/eb026526`

> K. Spärck Jones, "A statistical interpretation of term specificity and its
> application in retrieval," *Journal of Documentation*, vol. 28, no. 1,
> pp. 11–21, 1972, doi: 10.1108/eb026526.

Crossref returns this title in full capitals; the sentence-case form above is
the conventional rendering.

**Note the paper must make alongside these two.** TF-IDF and BM25 are
hand-implemented in `metrics/m4_baselines.py`, not taken from a library —
`scikit-learn` and `rank_bm25` are both absent from the environment. The
implementation uses log-tf with L2-normalised cosine ("ltc") and a *smoothed*
idf of `log(1 + N/df)`. The smoothing is a deliberate deviation worth one
clause: the unsmoothed BM25 idf goes negative for terms appearing in more than
half the documents, which in a single-role cohort is most of the vocabulary.

---

## Verified — software and model components (rows 4–9)

**faster-whisper (row 4)** — cite the model, then the implementation.
Model: ✅ verified, arXiv `2212.04356`

> A. Radford, J. W. Kim, T. Xu, G. Brockman, C. McLeavey, and I. Sutskever,
> "Robust Speech Recognition via Large-Scale Weak Supervision," arXiv preprint
> arXiv:2212.04356, 2022.

> SYSTRAN, "faster-whisper: Faster Whisper transcription with CTranslate2,"
> version 1.2.1, 2026. [Online]. Available:
> https://github.com/SYSTRAN/faster-whisper

The second entry is the software actually in the dependency set. `faster-whisper`
is a CTranslate2 reimplementation of the Whisper architecture, not a different
model, so the architecture citation is the first entry. The paper should say
which Whisper checkpoint was used — the configuration default is `base`
(`WHISPER_MODEL=base`).

**YuNet (row 5)** — ✅ verified, Crossref `10.1007/s11633-023-1423-y`

> W. Wu, H. Peng, and S. Yu, "YuNet: A Tiny Millisecond-level Face Detector,"
> *Machine Intelligence Research*, vol. 20, no. 5, pp. 656–665, 2023,
> doi: 10.1007/s11633-023-1423-y.

**SFace (row 6)** — ✅ verified, Crossref `10.1109/tip.2020.3048632`

> Y. Zhong, W. Deng, J. Hu, D. Zhao, X. Li, and D. Wen, "SFace:
> Sigmoid-Constrained Hypersphere Loss for Robust Face Recognition," *IEEE
> Transactions on Image Processing*, vol. 30, pp. 2587–2598, 2021,
> doi: 10.1109/tip.2020.3048632.

⚠ The DOI is `10.1109/tip.2020.3048632` — a **2020** DOI stem on a **2021**
article, because IEEE assigned it at early access. A `10.1109/TIP.2021.*` DOI
for this paper does not resolve; I checked and got a 404. Do not "correct" the
year in the DOI.

**MediaPipe (row 7)** — ✅ verified, arXiv `1906.08172`

> C. Lugaresi *et al.*, "MediaPipe: A Framework for Building Perception
> Pipelines," arXiv preprint arXiv:1906.08172, 2019.

If the paper describes the **face-landmark model specifically** rather than the
framework, this is the more precise citation — ✅ verified, arXiv `1907.06724`:

> Y. Kartynnik, A. Ablavatski, I. Grishchenko, and M. Grundmann, "Real-time
> Facial Surface Geometry from Monocular Video on Mobile GPUs," arXiv preprint
> arXiv:1907.06724, 2019.

The code uses `face_landmarker.task` for dense landmarks and the head-pose
matrix, so the second entry is the one that matches what is actually run. Citing
both is defensible: the framework and the model within it.

**YOLO11n / ultralytics (row 8)** — ⚠ **no peer-reviewed paper exists for
YOLO11.** Cite the software, and do not attribute it to a conference.

> G. Jocher and J. Qiu, "Ultralytics YOLO11," version 8.4.138, 2024. [Online].
> Available: https://github.com/ultralytics/ultralytics. Licensed under
> AGPL-3.0.

Installed-package metadata confirms version `8.4.138` and `License: AGPL-3.0`,
with source `https://github.com/ultralytics/ultralytics`. The author attribution
is from the project's own citation guidance rather than a Crossref record, which
is why this entry is marked differently from the nine above — there is no DOI to
check it against.

This entry is also where the licensing statement should point. Object detection
ships **disabled** (`PROCTORING_OBJECT_DETECTION=false`), so the default
deployment never executes the AGPL-3.0 component; enabling it takes on AGPL
network-use obligations.

**scrypt (row 9)** — ✅ verified, Crossref `10.17487/rfc7914`

> C. Percival and S. Josefsson, "The scrypt Password-Based Key Derivation
> Function," RFC 7914, Internet Engineering Task Force, 2016,
> doi: 10.17487/RFC7914.

The RFC is the right citation for an implementation claim. If the paper argues
about *why* a memory-hard KDF was chosen, Percival's 2009 BSDCan paper
"Stronger key derivation via sequential memory-hard functions" is the source for
the argument — I could not find a DOI for it, so cite it by venue and year and
verify independently, or rely on the RFC alone.

---

## NOT VERIFIED — legal instruments (row 15)

⚠ **Both entries below need checking against EUR-Lex before submission.**
EUR-Lex returned HTTP 202 with an empty response body to three different request
forms today, so I could not read the official title block. The forms below are
the standard citation shape and the regulation numbers are the ones in common
use, but **I have not confirmed the exact official wording, OJ L number, date or
page range**, and I am not going to present an unverified official title as
verified.

> Regulation (EU) 2016/679 of the European Parliament and of the Council (General
> Data Protection Regulation), Art. 28 (processors). **[VERIFY against EUR-Lex:
> full official title, OJ L number, date, page range]**

> Regulation (EU) 2024/1689 of the European Parliament and of the Council
> (Artificial Intelligence Act), Annex III (high-risk systems — employment and
> worker management). **[VERIFY against EUR-Lex: full official title, OJ L
> number, date, page range, and the exact Annex III point number for
> employment]**

To verify, open `https://eur-lex.europa.eu` in a browser and search the CELEX
identifiers `32016R0679` and `32024R1689`. Copy the title block and the OJ
reference line exactly as printed. The Annex III point number for employment
matters — §VIII cites the category, so cite the point, not just the Annex.

---

## BibTeX

```bibtex
@article{jarvelin2002ndcg,
  author  = {J{\"a}rvelin, Kalervo and Kek{\"a}l{\"a}inen, Jaana},
  title   = {Cumulated gain-based evaluation of {IR} techniques},
  journal = {ACM Transactions on Information Systems},
  volume  = {20}, number = {4}, pages = {422--446}, year = {2002},
  doi     = {10.1145/582415.582418}
}

@article{kendall1945ties,
  author  = {Kendall, M. G.},
  title   = {The treatment of ties in ranking problems},
  journal = {Biometrika},
  volume  = {33}, number = {3}, pages = {239--251}, year = {1945},
  doi     = {10.1093/biomet/33.3.239}
}

@article{robertson2009bm25,
  author  = {Robertson, Stephen and Zaragoza, Hugo},
  title   = {The Probabilistic Relevance Framework: {BM25} and Beyond},
  journal = {Foundations and Trends in Information Retrieval},
  volume  = {4}, number = {1--2}, pages = {1--174}, year = {2009},
  doi     = {10.1561/1500000019}
}

@article{sparckjones1972idf,
  author  = {Sp{\"a}rck Jones, Karen},
  title   = {A statistical interpretation of term specificity and its
             application in retrieval},
  journal = {Journal of Documentation},
  volume  = {28}, number = {1}, pages = {11--21}, year = {1972},
  doi     = {10.1108/eb026526}
}

@article{radford2022whisper,
  author  = {Radford, Alec and Kim, Jong Wook and Xu, Tao and Brockman, Greg
             and McLeavey, Christine and Sutskever, Ilya},
  title   = {Robust Speech Recognition via Large-Scale Weak Supervision},
  journal = {arXiv preprint arXiv:2212.04356},
  year    = {2022}
}

@misc{fasterwhisper,
  author = {{SYSTRAN}},
  title  = {faster-whisper: Faster Whisper transcription with {CTranslate2}},
  note   = {Version 1.2.1},
  year   = {2026},
  url    = {https://github.com/SYSTRAN/faster-whisper}
}

@article{wu2023yunet,
  author  = {Wu, Wei and Peng, Hanyang and Yu, Shiqi},
  title   = {{YuNet}: A Tiny Millisecond-level Face Detector},
  journal = {Machine Intelligence Research},
  volume  = {20}, number = {5}, pages = {656--665}, year = {2023},
  doi     = {10.1007/s11633-023-1423-y}
}

@article{zhong2021sface,
  author  = {Zhong, Yaoyao and Deng, Weihong and Hu, Jiani and Zhao, Dongyue
             and Li, Xian and Wen, Dongchao},
  title   = {{SFace}: Sigmoid-Constrained Hypersphere Loss for Robust Face
             Recognition},
  journal = {IEEE Transactions on Image Processing},
  volume  = {30}, pages = {2587--2598}, year = {2021},
  doi     = {10.1109/tip.2020.3048632}
}

@article{lugaresi2019mediapipe,
  author  = {Lugaresi, Camillo and Tang, Jiuqiang and Nash, Hadon and
             McClanahan, Chris and Uboweja, Esha and Hays, Michael and
             Zhang, Fan and Chang, Chuo-Ling and Yong, Ming Guang and
             Lee, Juhyun and Chang, Wan-Teh and Hua, Wei and Georg, Manfred
             and Grundmann, Matthias},
  title   = {{MediaPipe}: A Framework for Building Perception Pipelines},
  journal = {arXiv preprint arXiv:1906.08172},
  year    = {2019}
}

@article{kartynnik2019facemesh,
  author  = {Kartynnik, Yury and Ablavatski, Artsiom and Grishchenko, Ivan
             and Grundmann, Matthias},
  title   = {Real-time Facial Surface Geometry from Monocular Video on
             Mobile {GPU}s},
  journal = {arXiv preprint arXiv:1907.06724},
  year    = {2019}
}

@misc{ultralytics2024yolo11,
  author = {Jocher, Glenn and Qiu, Jing},
  title  = {{Ultralytics YOLO11}},
  note   = {Version 8.4.138, AGPL-3.0},
  year   = {2024},
  url    = {https://github.com/ultralytics/ultralytics}
}

@techreport{percival2016scrypt,
  author      = {Percival, C. and Josefsson, S.},
  title       = {The scrypt Password-Based Key Derivation Function},
  number      = {RFC 7914},
  institution = {Internet Engineering Task Force},
  year        = {2016},
  doi         = {10.17487/RFC7914}
}

% NOT VERIFIED -- confirm the official title, OJ reference and Annex point
% against EUR-Lex (CELEX 32016R0679 and 32024R1689) before submission.
@misc{gdpr2016,
  title = {Regulation (EU) 2016/679 (General Data Protection Regulation)},
  note  = {Art. 28, processors. VERIFY official title and OJ reference},
  year  = {2016}
}

@misc{aiact2024,
  title = {Regulation (EU) 2024/1689 (Artificial Intelligence Act)},
  note  = {Annex III, employment and worker management. VERIFY official title,
           OJ reference and exact Annex III point},
  year  = {2024}
}
```

---

## Still open on row 3

The **literature survey** half of row 3 is not supplied: automated and video
interviewing, LLM interview assessment, remote proctoring, LLM-as-judge validity
and human agreement.

This cannot be answered from the repository, and filling it with
plausible-looking titles is the single worst thing either of us could do to this
paper — the first draft already contained an orphaned 24-entry reference list
with entries whose venues read `"IEEE Trans., 2021"` and `"in IEEE Conf., 2021"`,
and deleting that list was the right call. Please run the search properly, or
narrow the §II-E research-gap claim to what the seven verified references
actually support.
