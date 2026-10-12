import pandas as pd

df = pd.read_csv('data/interim/tagesschau_umfrage-alter.csv', sep=',')

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)

print(df.head(50))


