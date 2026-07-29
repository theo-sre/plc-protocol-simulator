# Simulateur de valeurs fictives → Modbus TCP / OPC UA / BACnet/IP / S7

Génère des valeurs simulées (booléens, entiers, réels) selon des formes
paramétrables — sinusoïde, rampe, triangle, créneau, aléatoire, séquence,
formule, valeur manuelle — et les publie **en même temps** sur quatre serveurs :
**Modbus TCP**, **OPC UA**, **BACnet/IP** et **S7comm** (le PC se fait passer
pour un automate Siemens). Accessibles depuis le réseau par n'importe quel
automate, superviseur ou appareil de test.

Une IHM web permet de suivre les valeurs en direct, de forcer manuellement
n'importe quel tag, d'ajouter ou supprimer des variables à chaud et de consulter
les détails de connexion de chacune.

---

## 1. Installation

Python 3.10 ou plus est requis (testé avec Python 3.12, pymodbus 3.14,
asyncua 2.0).

```bash
winget install --id Python.Python.3.12 -e
```

Fermer puis rouvrir le terminal, sinon `python` reste introuvable. Puis :

```bash
cd "C:\Users\Theo user\Desktop\DataTest"; python -m pip install -r requirements.txt
```

> Windows PowerShell 5.1 ne connaît pas l'opérateur `&&` : enchaîner les
> commandes avec `;`.

## 2. Lancement

```bash
python main.py
```

Ou double-clic sur `start.bat`, qui installe les dépendances au premier
démarrage puis lance le programme.

Au démarrage, le programme affiche la table d'adressage complète et les
adresses réseau sur lesquelles les serveurs répondent :

```
TAG                      TYPE      MODBUS                 OPC UA                       GENERATEUR
temperature              float32   holding 0-1 (FC3/FC16) ns=2;s=Simulation.temperature sine
marche_pompe             bool      coil 0 (FC1/FC5)       ns=2;s=Simulation.marche_pompe toggle
...

Accessible depuis le reseau aux adresses :
  Modbus TCP  192.168.1.42:502  (unit id 1)
  OPC UA      opc.tcp://192.168.1.42:4840/freeopcua/server/
  IHM web     http://192.168.1.42:8080
```

Options utiles :

| Option | Effet |
|---|---|
| `-c autre.yaml` | utilise une autre configuration |
| `--list` | affiche la table d'adressage et quitte |
| `--modbus-port 5020` | change le port Modbus (si 502 est déjà pris) |
| `--opcua-port 4841` | change le port OPC UA |
| `--bacnet-port 47809` | change le port BACnet/IP |
| `--bacnet-host 192.168.1.10` | force l'interface réseau utilisée par BACnet |
| `--s7-port 1102` | change le port S7 |
| `--scan-ms 100` | accélère le rafraîchissement |
| `--no-modbus` / `--no-opcua` / `--no-bacnet` / `--no-s7` / `--no-web` | désactive un serveur |
| `-v` | logs détaillés |

## 3. Rendre les valeurs lisibles depuis un autre appareil

Les serveurs écoutent sur `0.0.0.0`, donc sur toutes les cartes réseau. Il
reste à ouvrir le pare-feu Windows (PowerShell **en administrateur**) :

```powershell
New-NetFirewallRule -DisplayName "Simulateur Modbus TCP" -Direction Inbound -Protocol TCP -LocalPort 502 -Action Allow
```

```powershell
New-NetFirewallRule -DisplayName "Simulateur OPC UA" -Direction Inbound -Protocol TCP -LocalPort 4840 -Action Allow
```

```powershell
New-NetFirewallRule -DisplayName "Simulateur BACnet/IP" -Direction Inbound -Protocol UDP -LocalPort 47808 -Action Allow
```

```powershell
New-NetFirewallRule -DisplayName "Simulateur S7comm" -Direction Inbound -Protocol TCP -LocalPort 102 -Action Allow
```

```powershell
New-NetFirewallRule -DisplayName "Simulateur IHM web" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
```

> Attention : BACnet est en **UDP**, pas en TCP — c'est bien `-Protocol UDP`.
> Et si la carte réseau est classée *Public* par Windows, ajouter
> `-Profile Any` à chaque règle, sinon elle ne s'applique pas.

L'appareil distant se connecte ensuite sur :

| Protocole | Adresse |
|---|---|
| Modbus TCP | `IP_DU_PC:502`, unit id 1 |
| OPC UA | `opc.tcp://IP_DU_PC:4840/freeopcua/server/`, anonyme |
| BACnet/IP | `IP_DU_PC:47808` (UDP), device id 599 |
| S7comm | `IP_DU_PC:102`, rack 0 / slot 1, DB1 |

Pour vérifier depuis le PC lui-même ou depuis une autre machine :

```bash
python test_client.py 192.168.1.42
```

## 4. Configurer les tags

Tout se passe dans `config.yaml`. Un tag minimal :

```yaml
- name: temperature
  dtype: float32
  unit: degC
  generator: {type: sine, amplitude: 25, offset: 75, period: 60}
```

### Champs d'un tag

| Champ | Rôle |
|---|---|
| `name` | identifiant (lettres/chiffres/underscore) — sert de nom de nœud OPC UA |
| `dtype` | `bool`, `int16`, `uint16`, `int32`, `uint32`, `float32`, `float64` |
| `generator` | forme du signal (voir ci-dessous) |
| `address` | adresse Modbus imposée ; sinon attribuée automatiquement |
| `scale` | multiplicateur (ex. `100` pour transmettre 23,45 °C en entier 2345) |
| `deadband` | variation minimale avant publication |
| `writable` | autorise l'écriture par un client Modbus/OPC UA (défaut : oui) |
| `modbus` / `opcua` | mettre `false` pour exclure le tag d'un protocole |
| `description`, `unit` | affichés dans l'IHM et dans la description OPC UA |

### Générateurs numériques

| Type | Paramètres |
|---|---|
| `constant` | `value` |
| `manual` | `value` — piloté depuis l'IHM ou par écriture réseau |
| `sine` / `cosine` | `amplitude`, `offset`, `period` (s), `phase` (deg) |
| `triangle` | `min`, `max`, `period` |
| `sawtooth` | `min`, `max`, `period` |
| `square` | `min`, `max`, `period`, `duty` (0..1) |
| `ramp` | `start`, `rate` (unités/s), `min`, `max`, `mode`: `wrap` \| `clamp` \| `bounce` |
| `counter` | `start`, `step`, `period` (s), `max` (rebouclage) |
| `random` | `min`, `max`, `period` |
| `gaussian` | `mean`, `sigma`, `period` |
| `random_walk` | `start`, `step` (amplitude max/s), `min`, `max` |
| `sequence` | `steps: [{value, duration}, …]`, `loop` |
| `expression` | `expr` — formule Python |

« Toujours monter » = `ramp` avec `mode: clamp` (bloque en haut) ou
`mode: wrap` (repart de zéro) ou `mode: bounce` (redescend).

### Générateurs booléens

| Type | Paramètres |
|---|---|
| `toggle` | `period`, `duty` (0..1), `invert` — allume/éteint périodiquement |
| `pulse` | `period`, `width` (s) — impulsion brève |
| `random_bool` | `probability` (0..1), `period` |
| `sequence` | `steps: [{value: true, duration: 4}, …]` |
| `manual` / `constant` / `expression` | idem numériques |

### Formules

Le générateur `expression` évalue une formule Python à chaque cycle. Sont
disponibles : `t` (secondes depuis le démarrage), `dt`, le module `math`, et
**les tags déclarés plus haut dans le fichier** :

```yaml
- name: temperature_f
  dtype: float32
  generator: {type: expression, expr: "temperature * 1.8 + 32"}

- name: alarme_temperature
  dtype: bool
  generator: {type: expression, expr: "temperature > 90"}
```

## 5. IHM web

`http://localhost:8080` — la table se rafraîchit toutes les 500 ms.

En haut de page, un bandeau rappelle les **points de connexion des quatre
serveurs** (adresse, port, unit id / device id / DB), calculés avec le nom
d'hôte par lequel tu consultes la page : depuis un autre poste, les adresses
affichées sont directement utilisables. À droite, un voyant indique si le
simulateur répond, avec sa durée de fonctionnement.

Chaque ligne affiche la valeur courante et une **courbe de tendance** des 30
dernières secondes — sinusoïdes, rampes et créneaux se reconnaissent d'un coup
d'œil, et les booléens sont tracés en marches d'escalier. Un badge orange
signale une valeur forcée. Le thème suit celui du système, clair ou sombre.

### Détails de connexion

**Cliquer sur une ligne** la déplie et affiche tout ce qu'il faut pour s'y
connecter :

* les paramètres du générateur ;
* côté **Modbus** : adresse IP et port, unit id, zone (coil / holding
  register), numéro de registre, codes fonction de lecture et d'écriture,
  ordre des mots, échelle ;
* côté **OPC UA** : endpoint, NodeId complet, namespace, type, sécurité ;
* deux **extraits Telegraf** prêts à coller — un pour `inputs.modbus` avec
  `byte_order`, `data_type` et le `scale` inverse déjà calculés, un pour
  `inputs.s7comm` avec l'adresse DB — chacun avec un bouton *copier* ;
* un bouton *Supprimer cette variable*.

> Le bouton *copier* utilise l'API presse-papier du navigateur, disponible
> uniquement en contexte sécurisé : elle fonctionne en `localhost` et en HTTPS.
> Depuis un autre poste en HTTP simple, le bouton bascule sur une méthode de
> repli, et affiche « echec » si le navigateur la refuse aussi — le texte reste
> sélectionnable à la main.

L'adresse affichée est celle par laquelle tu consultes la page : si tu ouvres
l'IHM depuis un autre poste, les endpoints affichés sont directement
utilisables.

### Ajouter / supprimer une variable

Le bouton **+ Ajouter une variable** ouvre un formulaire : nom, description,
unité, type de donnée, générateur — les champs de paramètres s'adaptent au
générateur choisi — plus l'adresse Modbus (laissée vide = attribuée
automatiquement), l'échelle et les options (écriture, Modbus, OPC UA).

Pour supprimer, deux moyens :

* **une seule variable** — la déplier et cliquer sur *Supprimer cette
  variable* ;
* **plusieurs d'un coup** — le bouton **Supprimer des variables** fait
  apparaître une colonne de cases à cocher (avec une case « tout cocher » dans
  l'en-tête). Le bouton *Supprimer la sélection (n)* affiche le décompte en
  direct ; il demande confirmation en listant les noms, puis supprime tout.
  *Annuler* quitte le mode sans rien toucher.

L'ajout et la suppression sont pris en compte **à chaud** : le registre Modbus,
le nœud OPC UA, l'objet BACnet et la zone du DB S7 sont créés ou supprimés sans
redémarrer. À la suppression, les zones Modbus et S7 sont remises à zéro pour
ne pas laisser une valeur fantôme.

Les changements sont enregistrés automatiquement (voir ci-dessous), donc ils
survivent à un redémarrage.

La liste des variables ne tient que dans la table Modbus déclarée
(`modbus.size`, 2000 par défaut) ; au-delà, l'ajout est refusé avec un message
explicite.

### Enregistrement automatique

Les ajouts, suppressions et consignes manuelles sont **enregistrés
automatiquement dans `config.yaml`** : l'état survit à un redémarrage du
simulateur ou du PC, sans rien avoir à cliquer. L'état apparaît dans le
sous-titre (*enregistré automatiquement*, *enregistrement en cours…*).

L'écriture est **différée de 2 secondes** après la dernière modification, pour
regrouper une rafale de changements en une seule écriture, et **atomique**
(fichier temporaire puis remplacement) : une coupure de courant en pleine
écriture ne peut pas laisser un `config.yaml` tronqué.

Ce qui est persisté :

| Élément | Persisté |
|---|---|
| Ajout / suppression de variable | oui |
| Consigne d'un générateur `manual` | oui |
| Valeur **forcée** sur un autre générateur | non — c'est un écrasement temporaire |

Le bouton **Enregistrer maintenant** force une écriture immédiate. Pour
désactiver l'automatisme, mettre `autosave: false` en tête de `config.yaml`.

> Le fichier réécrit **perd les commentaires**. La dernière version écrite à la
> main est conservée dans `config.yaml.bak` — et elle y reste : une fois que
> `config.yaml` porte l'en-tête du simulateur, le `.bak` n'est plus écrasé,
> même après plusieurs redémarrages.

### Config Telegraf complète

Le bouton **Config Telegraf** ouvre un `[[inputs.modbus]]` couvrant tous les
tags : un bloc `holding_registers` par valeur numérique, un bloc `coils` par
booléen. Il ne reste qu'à remplacer `IP_DU_PC` et à coller dans
`/etc/telegraf/telegraf.conf`. Accessible aussi en ligne de commande :

```bash
curl http://IP_DU_PC:8080/api/telegraf
```

## 6. Pilotage manuel

Trois moyens équivalents :

1. **IHM web** — champ de saisie pour les valeurs numériques, bouton
   *Basculer* pour les booléens, bouton *Libérer* pour rendre la main au
   générateur.
2. **API HTTP** :

```bash
curl -X POST http://localhost:8080/api/set -H "Content-Type: application/json" -d "{\"name\":\"consigne_vitesse\",\"value\":1800}"
```

   Routes disponibles :

   | Route | Effet |
   |---|---|
   | `GET /api/state` | état complet (tags + infos serveurs) |
   | `GET /api/catalog` | générateurs disponibles et leurs paramètres |
   | `GET /api/telegraf` | config Telegraf générée |
   | `POST /api/set` | `{name, value}` — écrit ou force une valeur |
   | `POST /api/toggle` | `{name}` — bascule un booléen |
   | `POST /api/release` | `{name}` — annule un forçage |
   | `POST /api/add` | même contenu qu'une entrée `tags:` du YAML |
   | `POST /api/remove` | `{name}` |
   | `POST /api/save` | réécrit `config.yaml` |

3. **Écriture Modbus (FC5/FC6/FC16) ou OPC UA** depuis l'appareil distant : la
   valeur écrite est relue au cycle suivant et devient la valeur du tag.

Un tag `manual` conserve simplement sa consigne ; un tag doté d'un générateur
passe en **forcé** (affiché en orange) jusqu'à ce qu'on le libère.

## 7. Correspondance BACnet/IP

* Les valeurs numériques deviennent des objets **`analog-value`**
  (`present-value` en REAL — donc un `uint32` est vu comme un flottant).
* Les booléens deviennent des objets **`binary-value`** (`active` /
  `inactive`).
* L'`object-name` BACnet est le nom du tag ; la `description` et l'unité sont
  reprises. L'unité du tag est traduite en `EngineeringUnits` quand elle est
  reconnue (`degC` → `degrees-celsius`, `bar` → `bars`, `m3/h` →
  `cubic-meters-per-hour`, `kW` → `kilowatts`, …), sinon `no-units`.
* Les numéros d'instance sont attribués dans l'ordre du fichier — AV1, AV2…
  pour les numériques, BV1, BV2… pour les booléens. Pour les figer, mettre
  `bacnet_instance:` sur le tag.
* Le serveur répond aux **Who-Is**, **ReadProperty**, **ReadPropertyMultiple**
  et **WriteProperty** (sur `present-value`, si `writable` est vrai).

### Choix de l'interface réseau

`bacnet.host` accepte trois formes :

| Valeur | Effet |
|---|---|
| `0.0.0.0` (défaut) | écoute sur **toutes** les cartes réseau, VPN compris (Tailscale, WireGuard…), comme Modbus et OPC UA |
| `auto` | seulement la carte qui porte la route Internet par défaut |
| une IP | seulement cette carte |

`prefix_length` (24 par défaut) ne sert qu'aux modes `auto` et IP fixe : il
calcule l'adresse de diffusion pour les Who-Is. Il est ignoré avec `0.0.0.0`.

> `auto` est fragile : si le routage du PC change (Wi-Fi qui bascule sur un
> autre réseau, VPN qui monte), l'interface choisie change au redémarrage
> suivant et les clients ne trouvent plus le simulateur. C'est pourquoi le
> défaut est `0.0.0.0`.

**Découverte et VPN** : un Who-Is en **unicast** (adressé directement à l'IP du
simulateur) fonctionne partout, y compris à travers Tailscale. Un Who-Is en
**diffusion** ne traverse pas un VPN maillé — la découverte automatique ne
marchera donc pas depuis un poste distant, mais les lectures directes oui. Côté
client, il faut alors renseigner l'IP du simulateur au lieu de compter sur la
découverte.

**BACnet MS/TP n'est pas géré** : c'est un bus série RS-485, pas de
l'Ethernet. Il faudrait un adaptateur USB↔RS-485 sur le PC et un câblage
dédié — voir la fin du README.

## 8. Correspondance S7comm

Le simulateur ouvre un serveur ISO-on-TCP (RFC1006) sur le port 102 et expose
un bloc de données, **DB1** par défaut. L'organisation du DB est la suivante :

```
octets 0 .. bool_bytes-1   les booléens, un bit chacun    DB1.X<octet>.<bit>
octets bool_bytes ..       les valeurs numériques,        DB1.<type><offset>
                           alignées sur 2 octets
```

Correspondance des types (tout est en big-endian, comme sur un vrai automate) :

| `dtype` | Type S7 | Adresse | Taille |
|---|---|---|---|
| `bool` | BOOL | `DB1.X0.3` | 1 bit |
| `int16` | INT | `DB1.I40` | 2 octets |
| `uint16` | WORD | `DB1.W42` | 2 octets |
| `int32` | DINT | `DB1.DI56` | 4 octets |
| `uint32` | DWORD | `DB1.DW36` | 4 octets |
| `float32` | REAL | `DB1.R16` | 4 octets |
| `float64` | LREAL | `DB1.LR60` | 8 octets — S7-1200/1500 uniquement |

Les offsets sont attribués automatiquement dans l'ordre du fichier ; pour les
figer, mettre `s7_offset:` (et `s7_bit:` pour un booléen) sur le tag.
`python main.py --list` affiche le plan mémoire complet.

`rack` et `slot` sont purement informatifs côté simulateur — il accepte
n'importe quelle valeur — mais il faut les renseigner dans le client. La
convention est 0/1 pour un S7-1200/1500 et 0/2 pour un S7-300/400.

Le serveur accepte aussi les **écritures** (`db_write`) : la valeur écrite est
relue au cycle suivant et devient la valeur du tag, comme en Modbus. À noter
que le plugin Telegraf `inputs.s7comm` est une *entrée* — il ne fait que lire ;
écrire depuis Telegraf n'est pas prévu par ce plugin.

### Correctif de la poignée de main COTP

Le serveur pur Python de `python-snap7` 3.1.0 répond au *Connection Request*
par un *Connection Confirm* tronqué de 11 octets, sans aucun paramètre :

```
03 00 00 0b  06 d0 00 01 00 01 00
```

Un automate Siemens réel renvoie un CC de 22 octets reprenant la taille de
TPDU (`C0`) et les deux TSAP (`C1` appelant, `C2` appelé). snap7 — et **gos7**,
son portage Go utilisé par le plugin Telegraf `s7comm` — vérifient strictement
ce format et rejettent le CC tronqué avec `ISO : Invalid PDU received`. Les
clients laxistes, eux, se connectent sans broncher : le serveur semble
fonctionner alors que Telegraf ne peut pas s'y attacher.

`simulator/s7_sink.py` corrige ça **à l'exécution** (`patch_cotp_handshake()`),
sans modifier `site-packages` : le CR est reparsé pour récupérer ses
paramètres et le CC les reprend comme le ferait un vrai automate.

```
03 00 00 16  11 d0 00 01 00 01 00  c0 01 0a  c1 02 01 00  c2 02 01 02
```

Le correctif est idempotent et reste correct si `python-snap7` corrige le
problème de son côté.

> `bool_bytes` (16 par défaut, soit 128 booléens) réserve le début du DB aux
> bits. C'est ce qui permet d'ajouter un booléen à chaud sans décaler les
> valeurs numériques déjà placées.

## 9. Correspondance Modbus

* Les booléens vont dans les **coils** (FC1 lecture / FC5 écriture) et sont
  recopiés dans les **discrete inputs** (FC2).
* Les valeurs numériques vont dans les **holding registers** (FC3 / FC16) et
  sont recopiées dans les **input registers** (FC4).
* Les types 32 bits occupent 2 registres consécutifs, en big-endian
  (`word_order: little` dans la section `modbus` pour inverser les mots).
* Les adresses sont attribuées automatiquement dans l'ordre du fichier, sauf
  celles fixées avec `address:` — un chevauchement est détecté au démarrage.

`python main.py --list` affiche la table complète.

## 10. Structure du projet

| Fichier | Rôle |
|---|---|
| `main.py` | point d'entrée, options de ligne de commande |
| `config.yaml` | définition des tags et des serveurs |
| `simulator/generators.py` | toutes les formes de signal |
| `simulator/config.py` | chargement YAML, adressage, encodage registres |
| `simulator/engine.py` | boucle de calcul, forçages, ajout/suppression |
| `simulator/modbus_sink.py` | serveur Modbus TCP |
| `simulator/opcua_sink.py` | serveur OPC UA |
| `simulator/bacnet_sink.py` | serveur BACnet/IP |
| `simulator/s7_sink.py` | serveur S7comm |
| `simulator/webui.py` | IHM web et API HTTP |
| `test_client.py` | client de vérification Modbus + OPC UA |

## 11. Et BACnet MS/TP ?

MS/TP (*Master-Slave / Token-Passing*) est la variante **série** de BACnet :
elle circule sur une paire torsadée RS-485 à 9600–115200 bauds, pas sur
Ethernet. Un PC ne peut donc pas la parler sans matériel :

* un **adaptateur USB ↔ RS-485** (isolé de préférence) côté PC ;
* un bus RS-485 correctement câblé (paire torsadée, résistances de terminaison
  120 Ω aux deux extrémités, masse commune) jusqu'à l'appareil ;
* côté logiciel, `bacpypes3` sait faire du MS/TP, mais il faut fixer une
  adresse MAC de station (0–127), la vitesse du bus et le `max-master`, et ces
  réglages doivent correspondre exactement à ceux des autres équipements du
  bus.

Si tu as l'adaptateur, l'ajout se fait dans `simulator/bacnet_sink.py` : il
suffit de remplacer le `NetworkPortObject` IPv4 par un port MS/TP — les objets
Analog Value / Binary Value et toute la logique restent identiques.

## 12. Dépannage

| Symptôme | Cause probable |
|---|---|
| `pymodbus >= 3.14 est requis` | `python -m pip install --upgrade "pymodbus>=3.14"` — l'API datastore a changé (SimData/SimDevice) |
| `Impossible de demarrer le serveur modbus` | port 502 déjà utilisé → `--modbus-port 5020` |
| Le client BACnet ne répond pas depuis un autre réseau ou un VPN | `bacnet.host` vaut `auto` ou une IP fixe → mettre `0.0.0.0` pour écouter sur toutes les interfaces |
| Le client BACnet ne découvre pas le device | règle pare-feu **UDP** 47808 manquante, ou découverte par diffusion à travers un VPN (utiliser l'IP directe) |
| `device id 599` déjà pris sur le réseau | changer `bacnet.device_id` (il doit être unique) |
| Le client S7 ne se connecte pas | port 102 déjà pris (un autre logiciel Siemens ?) → `--s7-port`, ou pare-feu TCP 102 |
| `ISO : Invalid PDU received` côté client S7 | le correctif COTP n'est pas actif — vérifier que `patch_cotp_handshake()` s'exécute bien au démarrage du serveur S7 (§8) |
| `plus de bit libre dans le DB S7` | augmenter `s7.bool_bytes` dans `config.yaml` |
| L'appareil distant ne voit rien | règle de pare-feu manquante (§3) |
| Client OPC UA qui refuse l'endpoint | mettre l'IP réelle dans `opcua.host` au lieu de `0.0.0.0` |
| Valeurs float incohérentes côté automate | inverser `word_order` (`big` ↔ `little`) |

