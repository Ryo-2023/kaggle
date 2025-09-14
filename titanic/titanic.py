import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import lightgbm as lgb
from catboost import CatBoostClassifier

train_data_path = "titanic/train.csv"
test_data_path = "titanic/test.csv"

train_data = pd.read_csv(train_data_path)
test_data = pd.read_csv(test_data_path)

women = train_data.loc[train_data.Sex=="female"]["Survived"]
rate_women = sum(women) / len(women)

y = train_data["Survived"]  # target value

features = ["Pclass","Sex","Age","Fare"]
x_train = pd.get_dummies(train_data[features])
x_test = pd.get_dummies(test_data[features]).reindex(columns = x_train.columns, fill_value=0)

# model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=1)
# model = lgb.LGBMClassifier(n_estimators=100, max_depth=5, random_state=1, verbosity=-1
model = CatBoostClassifier(n_estimators=100, max_depth=5, random_state=1, verbose=0)
                           
model.fit(x_train,y)
predictions = model.predict(x_test)

output = pd.DataFrame({'PassengerId': test_data.PassengerId, 'Survived': predictions})
output.to_csv('titanic/submission.csv', index=False)
print("Your submission was successfully saved!")


