# lecteur-local

Lecteur de textes par un modèle de langue OUVERT tournant en local sur les machines de GitHub Actions (llama.cpp, sans
carte graphique, sans clé ni coût), pour le bot de prévision
[metac-bot-template](https://github.com/Othman-Benbrahim/metac-bot-template). Décision de l'auteur du 2026-11-21
(19 h 48) : un dépôt séparé pour ne pas charger le dépôt du bot ; le bot lit les résultats publiés ici.

Principes :
- le modèle LIT, il ne juge pas : il extrait des faits structurés (date, acteur, événement, chiffre, lien vers la
  source) ; les probabilités restent calculées par le code du bot ;
- les textes lus ne sont jamais gardés : seuls les champs extraits et le lien vers la source sont écrits ;
- chaque extraction est contrôlée là où une vérité est connue (par exemple les identifiants de failles, relus par une
  expression régulière) : le taux de fidélité est publié avec les résultats ;
- robots.txt respecté pour toute page lue.

Premier passage : flux public du CERT-FR (ANSSI) ; modèle Qwen2.5-7B-Instruct (licence Apache 2.0), quantifié Q4_K_M.

Résultats : `resultats/faits-cyber.json`, `resultats/fidelite.json`.
