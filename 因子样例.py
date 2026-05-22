import numpy as np
import pandas as pd
import joblib


def Calculate_IC(x, y):
    N = x.shape[0]
    IC = np.full((N,), fill_value=np.nan)
    for i in range(N):
        idx = np.where(np.logical_and(~np.isnan(x[i, :]), ~np.isnan(y[i, :])))[0]
        IC[i] = np.corrcoef(x[i, idx], y[i, idx])[0, 1]

    return IC

FileNameSave = './DailyData20240102open.bin'

VarName = ['I_D_AMOUNT', 'I_D_VOLUME', 'I_D_CLOSE_ORI', 'Label']
with open(FileNameSave, 'rb') as f:
    dat_dict = joblib.load(f)
data = dict(zip(VarName, list(map(lambda x: dat_dict[x], VarName))))

I_D_AMOUNT = data['I_D_AMOUNT']
I_D_VOLUME = data['I_D_VOLUME']
I_D_CLOSE_ORI = data['I_D_CLOSE_ORI']
Label = data['Label']
I_D_VWAP = I_D_AMOUNT / I_D_VOLUME

bias = np.array(pd.DataFrame(I_D_VWAP / I_D_CLOSE_ORI - 1).rolling(window=20).mean().values)

IC = Calculate_IC(bias, Label)


