"""
Machine-learning trade filter.

A gradient-boosted classifier that estimates P(target hit before stop) for a
candidate entry produced by the swing or scalp engine ("meta-labelling": the
engines pick direction, the model decides whether the pick is worth taking).

  features.py   — feature extraction shared by training and live scoring
  labeling.py   — triple-barrier outcome labels from historical 1m bars
  predictor.py  — loads the trained model and scores live candidates

Training: scripts/ml_fetch_bars.py → scripts/ml_train.py
"""
