# -*- coding: utf-8 -*-
"""Modelo CONSOLIDADO: base de 7 classes + cascata de especialistas binarios.

Cada especialista so re-decide onde a base cair nas classes-gatilho dele, e usa
sua propria lista de bandas (ex.: nativa×varzea usa terreno; os outros so as 25
espectrais). O vetor de entrada X esta na ordem completa self.bandas (com terreno);
cada sub-modelo seleciona suas colunas por indice. Auto-contido p/ importar no app.
"""
import numpy as np


class ClassificadorConsolidado:
    def __init__(self, base, bandas_base, especialistas, bandas):
        # especialistas: (nome, modelo, bandas_esp, [gatilho]) OU, com limiar de
        # confianca, (nome, modelo, bandas_esp, [gatilho], limiar, classe_positiva):
        # so marca a classe_positiva se proba >= limiar, senao a outra classe do
        # gatilho (mantem a precisao alta - ex.: silvicultura confiavel).
        self.base = base
        self.bandas = list(bandas)  # ordem completa do vetor de entrada
        self.idx_base = [self.bandas.index(b) for b in bandas_base]
        self.especialistas = []
        for esp in especialistas:
            nome, modelo, bandas_esp, gatilho = esp[0], esp[1], esp[2], esp[3]
            limiar = esp[4] if len(esp) > 4 else None
            pos = esp[5] if len(esp) > 5 else None
            idx = [self.bandas.index(b) for b in bandas_esp]
            self.especialistas.append((nome, modelo, idx, [int(g) for g in gatilho], limiar, pos))

    def predict(self, X):
        X = np.asarray(X)
        pred = self.base.predict(X[:, self.idx_base])
        for esp in self.especialistas:
            # compat: especialista pode ter 4 campos (sem limiar) ou 6 (com limiar)
            nome, modelo, idx, gatilho = esp[0], esp[1], esp[2], esp[3]
            limiar = esp[4] if len(esp) > 4 else None
            pos = esp[5] if len(esp) > 5 else None
            mask = np.isin(pred, gatilho)
            if not mask.any():
                continue
            Xs = X[mask][:, idx]
            if limiar is None:
                pred[mask] = modelo.predict(Xs)
            else:
                ip = list(modelo.classes_).index(int(pos))
                proba = modelo.predict_proba(Xs)[:, ip]
                outra = [g for g in gatilho if g != int(pos)][0]
                pred[mask] = np.where(proba >= limiar, int(pos), outra)
        return pred
