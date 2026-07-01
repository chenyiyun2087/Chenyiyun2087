import numpy as np
import pandas as pd

from scoreRank.core.bs_point_infer import BSPointInferrer


class _FixedModel:
    def predict_proba(self, values):
        return np.tile([[0.25, 0.75]], (len(values), 1))


def test_predict_zero_fills_trained_features_missing_from_upstream_data():
    inferrer = BSPointInferrer.__new__(BSPointInferrer)
    inferrer.feature_names = ["available_feature", "optional_adc_feature"]
    inferrer.use_scaler = False
    inferrer.buy_model = _FixedModel()
    inferrer.sell_model = _FixedModel()
    inferrer.buy_threshold = 0.5
    inferrer.sell_threshold = 0.5
    inferrer.load_features = lambda *_: pd.DataFrame(
        [{"stock_code": "000001", "ts_code": "000001.SZ", "available_feature": 1.0}]
    )

    result = inferrer.predict("20260701")

    assert len(result) == 1
    assert result.iloc[0]["buy_prob"] == 0.75
