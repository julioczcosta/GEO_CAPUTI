# -*- coding: utf-8 -*-
"""
Classe do MODELO HIERARQUICO de uso do solo (Cerrado), auto-contida para o app.

O modelo salvo (.joblib) e uma instancia de ClassificadorHierarquico. Para o
joblib CARREGAR no app, este arquivo (modelo_hierarquico.py) precisa estar
importavel no projeto do app - so ele, sem depender do pipeline de coleta.

Como funciona: um RandomForest de 8 classes decide o uso; sempre que ele diz
lavoura OU pastagem, um segundo RandomForest ESPECIALISTA (treinado so nessas
duas classes, com quantidades iguais) da a palavra final. Isso remove o vies
que o desbalanceo de treino (muito mais pixel de lavoura que de pastagem)
causava - o modelo parava de "chutar lavoura na duvida".
"""

import numpy as np


class ClassificadorHierarquico:
    def __init__(self, base, especialista, bandas, cod_lavoura, cod_pastagem,
                 classes=None):
        self.base = base                    # RandomForest 8 classes
        self.especialista = especialista    # RandomForest binario lavoura x pastagem
        self.bandas = list(bandas)          # ORDEM exata das colunas no predict
        self.cod_lavoura = int(cod_lavoura)
        self.cod_pastagem = int(cod_pastagem)
        self.classes = classes or {}        # {codigo: nome}

    def predict(self, X):
        """X: array (n_amostras, n_bandas) na ordem de self.bandas."""
        X = np.asarray(X)
        pred = self.base.predict(X)
        mask = (pred == self.cod_lavoura) | (pred == self.cod_pastagem)
        if mask.any():
            pred[mask] = self.especialista.predict(X[mask])
        return pred

    def nomes(self, codigos):
        return [self.classes.get(int(c), str(c)) for c in codigos]
