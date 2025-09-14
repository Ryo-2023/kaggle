import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.model_selection import KFold
from sklearn.metrics import mean_squared_error
from tqdm import tqdm

train_data_path = "house-prices-advanced-regression-techniques/train.csv"
test_data_path = "house-prices-advanced-regression-techniques/test.csv"

train_data = pd.read_csv(train_data_path)
test_data = pd.read_csv(test_data_path)

# 目的変数（対数変換）
y = train_data["SalePrice"]
y_log = np.log1p(y)

# 特徴量: Id, SalePrice を除く全列
features = [c for c in train_data.columns if c not in ["SalePrice", "Id"]]
X = train_data[features].copy()
X_test = test_data[features].copy()

# 簡単な派生特徴
for df in (X, X_test):
    df["TotalSF"] = df.get("TotalBsmtSF", 0) + df.get("1stFlrSF", 0) + df.get("2ndFlrSF", 0)
    df["AgeHouse"] = df.get("YrSold", 0) - df.get("YearBuilt", 0)
    df["AgeRemod"] = df.get("YrSold", 0) - df.get("YearRemodAdd", 0)
    df["Bath"] = (df.get("FullBath", 0) + 0.5 * df.get("HalfBath", 0)
                  + df.get("BsmtFullBath", 0) + 0.5 * df.get("BsmtHalfBath", 0))
    df["TotalPorchSF"] = df.get("OpenPorchSF", 0) + df.get("EnclosedPorch", 0) + df.get("3SsnPorch", 0) + df.get("ScreenPorch", 0)

# 数値としてのカテゴリをカテゴリ化（例: MSSubClass を文字列へ）
for df in (X, X_test):
    if "MSSubClass" in df.columns:
        df["MSSubClass"] = df["MSSubClass"].astype(str)

# カテゴリ列: NaN→"Missing" へ、明示的に文字列化（CatBoost要件）
obj_cols = sorted(set(X.select_dtypes(include="object").columns) | set(X_test.select_dtypes(include="object").columns))
for df in (X, X_test):
    for c in obj_cols:
        df[c] = df[c].astype("object").where(df[c].notna(), "Missing").astype(str)

# CatBoostへ渡すカテゴリ列インデックス
cat_features = [i for i, c in enumerate(X.columns) if X[c].dtype == "object"]

# 5-fold CV + 早期停止 + 予測平均
kf = KFold(n_splits=5, shuffle=True, random_state=42)
oof = np.zeros(len(X))
test_pred_log = np.zeros(len(X_test))
fold_rmsle = []

params = dict(
    iterations=10000,
    learning_rate=0.03,
    depth=6,
    l2_leaf_reg=3.0,
    loss_function="RMSE",
    random_state=42,
    verbose=False
)

with tqdm(total=kf.get_n_splits(), desc="CV folds", unit="fold") as pbar:
    for fold, (trn_idx, val_idx) in enumerate(kf.split(X, y_log), 1):
        X_tr, X_val = X.iloc[trn_idx], X.iloc[val_idx]
        y_tr, y_val = y_log.iloc[trn_idx], y_log.iloc[val_idx]

        model = CatBoostRegressor(**params)
        model.fit(
            X_tr, y_tr,
            cat_features=cat_features,
            eval_set=(X_val, y_val),
            early_stopping_rounds=300,
            verbose=False
        )

        val_pred_log = model.predict(X_val)
        rmse = np.sqrt(mean_squared_error(y_val, val_pred_log))
        fold_rmsle.append(rmse)
        oof[val_idx] = val_pred_log

        test_pred_log += model.predict(X_test, ntree_end=model.best_iteration_) / kf.n_splits
        pbar.set_postfix(fold=fold, rmsle=f"{rmse:.4f}")
        pbar.update(1)

oof_rmsle = np.sqrt(mean_squared_error(y_log, oof))
print(f"OOF RMSLE (approx): {oof_rmsle:.5f} | folds: {[f'{r:.4f}' for r in fold_rmsle]}")

# 提出
preds = np.expm1(test_pred_log)
submission = pd.DataFrame({"Id": test_data["Id"], "SalePrice": preds})
submission.to_csv("house-prices-advanced-regression-techniques/submission.csv", index=False)
print("Submission saved: house-prices-advanced-regression-techniques/submission.csv")