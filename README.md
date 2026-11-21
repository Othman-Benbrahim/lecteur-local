# lecteur-local

Collecteur de textes pour le bot de prévision
[metac-bot-template](https://github.com/Othman-Benbrahim/metac-bot-template), dans un dépôt séparé pour ne pas
charger celui du bot (décision de l'auteur du 2026-11-21, 19 h 48).

Décision de l'auteur du 2026-11-21 (23 h 27 - 23 h 42) : abandon des lecteurs par modèle de langue (Gemma 4 E4B,
ReDefiNER, assemblage hybride). Leurs erreurs, imprévisibles, finiraient dans la documentation et la rendraient
inexploitable. Le dépôt passe à un lecteur sans modèle : des listes de mots téléchargées (noms, termes), un comptage
automatique et sériel, des comptes par jour, par source et par terme ; jamais le texte des articles. Les noms nouveaux
repérés dans les flux attendent dans un fichier de candidats et n'entrent dans les listes qu'après validation par
l'auteur.

Le code des anciens lecteurs reste dans l'historique (dernier état : commit abe038c) ; leurs mesures restent dans
`resultats/`.
