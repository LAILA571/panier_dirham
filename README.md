# 🏦 Panier du Dirham

Application Streamlit qui calcule, suit et compare les cours **théoriques** USD/MAD et EUR/MAD
(issus du panier de devises du Dirham) avec les cours **réels** publiés par Bank Al-Maghrib (BAM).
Les pondérations USD/EUR se modifient en direct, et l'effet sur les erreurs est immédiat.

---

## 1. Structure du projet

```
panier_dirham/
├── app.py                      # Interface Streamlit (aucun calcul financier ici)
├── requirements.txt            # Bibliothèques nécessaires
├── README.md                   # Ce fichier
├── data/
│   ├── historique_initial.xlsx # Votre fichier Excel d'origine (onglet « cours fx »)
│   ├── panier_dirham_history.csv  # Données RÉELLES (BAM + EUR/USD), jamais écrasées
│   ├── calculs_history.csv     # Résultats CALCULÉS (dérivés, régénérés à chaque mise à jour)
│   ├── last_update.json        # Statut de la dernière mise à jour
│   └── sauvegardes/            # Copies de sécurité horodatées avant chaque modification
├── src/
│   ├── bam_scraper.py          # Lecture de la page BAM (seul endroit dépendant du site BAM)
│   ├── investing_scraper.py    # EUR/USD Investing + import de secours CSV/Excel
│   ├── calculations.py         # Pondérations, cours théoriques, écarts, indicateurs (sans réseau)
│   ├── data_manager.py         # Excel, CSV, fusion sans doublons, sauvegardes, mise à jour
│   └── charts.py               # Graphiques Plotly
└── tests/
    └── test_panier.py          # 41 tests sur données FICTIVES
```

Chaque fichier a une seule responsabilité : le scraping ne fait pas de calcul, le calcul ne lit
aucun fichier ni aucun site.

---

## 2. Les formules

Notation : `w_USD` et `w_EUR` sont les pondérations (somme = 100 %). `t` est une date de publication
BAM. `t_input` est la dernière date **réelle commune** strictement antérieure à `t`.

**USD/MAD théorique**

```
USD_MAD_calcule(t) = w_USD × USD_MAD_BAM(t_input)
                   + w_EUR × ( EUR_MAD_BAM(t_input) ÷ EUR_USD(t_input) )
```

**EUR/MAD théorique**

```
EUR_MAD_calcule(t) = USD_MAD_calcule(t) × EUR_USD(t_input)
```

**Écarts**

```
Ecart_USD_MAD     = USD_MAD_calcule − USD_MAD_BAM_reel
Ecart_USD_MAD_pct = Ecart_USD_MAD ÷ USD_MAD_BAM_reel × 100
```
(même logique pour EUR/MAD)

### ⚠️ Correction importante de la formule

Votre cahier des charges indique une **multiplication** pour la partie EUR :
`EUR_MAD(t-1) × EUR_USD(t-1)`. Cette formule ne donne pas un cours cohérent. Avec les données du
29/10/2026 :

| Élément | Valeur |
|---|---|
| 1 EUR en MAD (BAM) | 10,9614 |
| 1 EUR en USD | 1,1180 |
| Multiplication (formule littérale) | 10,9614 × 1,1180 = **12,25 MAD par USD** ❌ |
| Division (formule retenue) | 10,9614 ÷ 1,1180 = **9,80 MAD par USD** ✅ |
| USD/MAD réel BAM | 9,8393 |

Le cours d'un dollar en dirhams s'obtient en **divisant** le cours de l'euro en dirhams par le cours
de l'euro en dollars : `1 USD = (MAD par EUR) ÷ (USD par EUR)`. C'est la formule utilisée dans
`src/calculations.py`. Si vous voulez absolument la formule littérale, dites-le-moi : je peux la
rendre disponible, mais le résultat serait faux de plus de 20 %.

### Exemple chiffré (pondération 40 / 60)

Inputs : USD/MAD = 9,8393 ; EUR/MAD = 10,9614 ; EUR/USD = 1,1180 (29/10/2026).

```
USD_calc = 0,40 × 9,8393 + 0,60 × (10,9614 ÷ 1,1180)
         = 3,93572 + 0,60 × 9,80447
         = 3,93572 + 5,88268
         = 9,8184 MAD
EUR_calc = 9,8184 × 1,1180 = 10,9770 MAD
```

Ces valeurs sont calculées à partir des données du 29/10 et comparées au cours BAM du jour de
publication suivant.

---

## 3. Mise à jour automatique

À chaque clic sur **« Mettre à jour les données »** :

1. l'historique réel est chargé depuis `panier_dirham_history.csv` ;
2. BAM est lu (les 2 dernières dates publiées sur la page officielle) ;
3. EUR/USD est lu sur Investing.com, ou dans le fichier de secours si vous en importez un ;
4. les nouvelles observations réelles sont fusionnées :
   - une date déjà présente **n'est jamais remplacée** ;
   - une valeur manquante peut être complétée par une valeur réelle ;
   - un conflit (valeur différente) est conservé et signalé ;
5. si quelque chose change, une **sauvegarde** de l'ancien fichier est créée, puis le fichier est réécrit ;
6. `calculs_history.csv` est régénéré (il ne contient aucune donnée réelle modifiée) ;
7. le statut (dates ajoutées, dates ignorées, erreurs) est enregistré dans `last_update.json`.

**Idempotence :** relancer la mise à jour plusieurs fois ne crée aucun doublon. La deuxième
exécution n'ajoute rien.

**Jours non ouvrés :** un week-end ou un jour férié n'a pas de publication BAM ; aucune ligne
n'est créée. L'input d'un calcul est toujours la dernière date réelle commune antérieure à la date
de publication. Le jour `t` n'est jamais utilisé pour calculer `t`.

---

## 4. Pondérations

Le curseur de la barre latérale règle le poids USD (0 à 100 %). Le poids EUR est calculé
automatiquement : `100 % − poids USD`. Quand vous le déplacez, tout est recalculé : cours
théoriques, écarts, graphiques, indicateurs. **Les données sources ne changent jamais.**

L'onglet « Pondérations et sensibilité » compare plusieurs scénarios (40/60, 50/50, 60/40, 30/70,
70/30 par défaut) et trace l'erreur MAE en fonction du poids USD.

### Indicateurs

| Indicateur | Signification |
|---|---|
| MAE | erreur absolue moyenne (en MAD) |
| RMSE | racine de l'erreur quadratique moyenne ; pénalise davantage les grosses erreurs |
| Biais moyen | sens de l'erreur : positif = le modèle surestime |
| Erreur moyenne en % | erreur signée moyenne, en pourcentage du cours réel |
| Écart-type | dispersion des erreurs autour de leur moyenne |
| Corrélation | lien entre cours calculés et cours réels (1 = parfait) |

Les indicateurs ne portent que sur les dates qui ont **à la fois** un cours calculé et un cours réel.

---

## 5. Points de vigilance (à lire)

- **BAM publie seulement les deux derniers jours sur la page.** Si vous ne lancez pas l'application
  au moins une fois par jour ouvré, des dates intermédiaires seront perdues. Elles ne peuvent
  alors être récupérées que par import manuel d'un fichier.
- **Structure de la page BAM** (vérifiée sur la page officielle) : tableau, dates JJ/MM/AAAA en
  colonnes, devises en lignes, virgule décimale. Si BAM change sa mise en page, seul
  `src/bam_scraper.py` doit être adapté. L'application garde alors l'historique et affiche
  un avertissement.
- **Investing.com** : si le site bloque la lecture automatique, le code ne le contourne pas. Il
  faut importer un export CSV/Excel dans la section « Source EUR/USD de secours ».
- **Dates Excel inversées** : dans votre fichier, les dates EUR/USD stockées comme dates Excel ont
  été lues en jour/mois inversés (`09/10/2026` pour le 10 septembre). Après correction, les 996
  dates communes concordent exactement. La case « Dates Excel inversées jour/mois » est cochée par
  défaut pour ce cas. Décochez-la pour un autre fichier dont les dates sont justes.
- **EUR/USD entier** : dans `Cours fx`, EUR/USD est stocké comme `11180` pour 1,1180. Le code le
  remet à l'échelle automatiquement.
- **Zéros dans le fichier Excel** : 73 dates ont une valeur `0` pour USD/MAD et EUR/MAD (surtout
  des jours fériés ou sans publication). Elles sont traitées comme des valeurs **manquantes**, jamais
  comme des cours.
- **Dates manquantes dans votre fichier** : `Cours fx` s'arrête au 29/10/2026, mais l'onglet EUR/USD
  contient déjà le 30/10. La mise à jour ajoutera le 30/10 BAM si la page l'affiche encore.

---

## 6. Lancer l'application dans Google Colab

Le plus simple : préparer une fois le fichier `panier_dirham.zip` (sur votre ordinateur, dézippez
puis compressez le dossier `panier_dirham`), puis suivre les cellules ci-dessous.

**Cellule 1 — installation**
```python
!pip install -q streamlit pandas numpy plotly openpyxl requests beautifulsoup4 lxml pytest
```

**Cellule 2 — importer le projet** (une fenêtre de téléversement s'ouvre)
```python
from google.colab import files
import os
uploaded = files.upload()           # choisissez panier_dirham.zip
!unzip -q -o panier_dirham.zip
%cd /content/panier_dirham
!ls
```
Votre fichier Excel est déjà inclus dans `data/historique_initial.xlsx`. Pour utiliser une
autre version, téléversez-la dans l'interface (barre latérale) ou remplacez ce fichier avant la
cellule 3.

**Cellule 3 — tests (facultatif, rapide)**
```python
!python -m pytest -q tests
```

**Cellule 4 — lancer Streamlit et l'exposer avec Cloudflare Tunnel**
Streamlit ne s'affiche pas directement dans une cellule Colab : on lance le serveur, puis un tunnel
public donne une adresse web à ouvrir dans votre navigateur.
```python
import subprocess, time, re, os

# 1. Télécharger cloudflared (une seule fois)
if not os.path.exists("/content/cloudflared"):
    subprocess.run(["wget", "-q", "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                    "-O", "/content/cloudflared"], check=True)
    os.chmod("/content/cloudflared", 0o755)

# 2. Lancer Streamlit en arrière-plan
streamlit_proc = subprocess.Popen(
    ["streamlit", "run", "app.py", "--server.port", "8501", "--server.headless", "true"],
    cwd="/content/panier_dirham",
    stdout=open("/content/streamlit.log", "w"), stderr=subprocess.STDOUT,
)
time.sleep(8)

# 3. Ouvrir le tunnel et afficher l'adresse publique
tunnel = subprocess.Popen(
    ["/content/cloudflared", "tunnel", "--url", "http://localhost:8501"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
url = None
for line in tunnel.stdout:
    match = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
    if match:
        url = match.group(0)
        break
print("👉 Ouvrez cette adresse dans votre navigateur :", url)
```
Gardez la cellule en cours d'exécution tant que vous utilisez l'application. Si Colab s'arrête,
relancez la cellule 4 : l'adresse change à chaque lancement.

**Mise à jour quotidienne :** ouvrez l'application et cliquez sur « 🔄 Mettre à jour les données ».
Le fichier `panier_dirham_history.csv` est conservé entre les sessions **seulement si vous le
téléchargez** (onglet « Données historiques ») et le replacez dans `data/` la fois suivante. Colab
efface les fichiers à la fin de la session.

---

## 7. Exporter les données

Onglet « 🗂️ Données historiques » : téléchargement CSV des données réelles, des calculs, et d'un
fichier Excel contenant tout. Les dates et les valeurs ne sont **pas arrondies** ; l'arrondi
n'existe que dans l'affichage.

---

## 8. Limites connues

- Les tests utilisent des données fictives. La lecture live de BAM n'a pas pu être vérifiée depuis
  l'environnement de construction (accès bloqué), mais la structure de la page a été vérifiée
  dans la page officielle.
- Investing.com peut bloquer la lecture automatique : prévoir l'import manuel en secours.
- Les résultats historiques sont des **estimations** : sur votre historique (pondération 40/60),
  l'erreur absolue moyenne est d'environ 0,018 MAD pour chaque devise. Ce n'est pas une prévision.
