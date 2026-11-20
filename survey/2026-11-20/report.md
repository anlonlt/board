# Étape 0 du kit : l'enquête (rapport 1) — 2026-11-20

Le projet : une application web personnelle d'auto-exploration (React/Vite, déployée sur Vercel), construite
uniquement avec Claude Code sur le web, par son propriétaire seul. Périmètre : les 49 sessions du dépôt du projet
(toutes confirmées par `get_session`), juillet 2026. Les fichiers du kit installés ce jour ne sont pas comptés comme
préexistants. Détails : `part1.md` (premier passage), `part2.md` (second passage, qui fait foi), `dev-report.md`
(en anglais).

## Rôles
- **Le propriétaire** décide, regarde les captures, fusionne chaque PR (66 fusionnées, 1 encore ouverte).
- **Les sessions** (49 : 41 web, 8 Android) codent, capturent, écrivent les passations. Une session = une étape
  d'un chantier, sa branche, sa PR. Aucune session coordinatrice, greffière ni relectrice ; aucun lien
  parent/enfant ; aucun `send_message`. Les opérations multi-agents ont tourné en sous-agents internes.

## Magasins
- **Docs du dépôt** : 46 fichiers Markdown (1,30 Mo), dont CLAUDE.md (24 Ko), six documents de passation (le plus
  long : 1 273 lignes, 13 sections HANDOFF datées), des doctrines de chantier, un carnet (88 Ko).
- **Skills** préexistantes : deux (98,6 Ko et 9 Ko).
- `docs/` pèse 212,7 Mo (772 JPG de captures, 14 PDF/EPUB sources).
- Git : 721 commits, 51 branches distantes, 7 branches en avance non fusionnées.
- Pas de dossier mémoire, pas de tableau (board), aucune tâche planifiée. Données de l'app : stockage local +
  export ; une base distante est codée mais dort.
- Transcriptions : les 49 sessions (la plus longue : 17 pages, 36 Mo, surtout des captures en base64).

## Canaux
- Propriétaire → session : messages tapés (web ou téléphone).
- Session → propriétaire : texte de chat, fichiers envoyés (captures), questions, état « bloquée » de la session,
  un artefact publié (maquette vivante), et surtout **les PR** avec leurs aperçus Vercel.
- Session → session : **uniquement les docs committés** (passations, « État & horizon » de CLAUDE.md).

## Hooks
Avant aujourd'hui : aucun dans le dépôt. Un hook Stop d'environnement (vérification git) s'est déclenché 4 fois
dans la session la plus longue.

## Règles
CLAUDE.md : 6 directives non négociables, 6 règles du protocole visuel (P1–P6), 8 lois de la doctrine de design,
plus des conventions et verrous de décisions. La skill visuelle porte 161 items, l'autre 41. **Seules les
vérifications npm (banc, témoins, garde, frontière, guichets, entretien) font respecter des règles par le code**, et
seulement quand une session les lance (pas de CI, pas de hook). Rien n'impose le protocole visuel hors du texte de
la skill.

## Faits tenus en double
| fait | lieux | dérive aujourd'hui |
|---|---|---|
| prochaine étape | CLAUDE.md « État & horizon », chaque passation, doctrines | CLAUDE.md a 4 jours de retard sur la dernière passation |
| ce qui attend le propriétaire | état « bloquée » des sessions, sections « décision requise » des passations | 8 sessions « bloquées » dont le travail a été fusionné : à archiver |
| décisions | CLAUDE.md, journal des décisions, doctrines | certaines viennent de chats hors dépôt |
| leçons | skill visuelle, passation du landing, doctrines | même leçon à deux endroits |
| état de la base | CLAUDE.md dit « aucune base » | des variables de base existent dans l'environnement cloud : propriétaire inconnu |

## Les 3 plus gros coûts (mesurés)
1. **Un contexte porté jusqu'à 728 k tokens** (moyenne 369 k) dans la session la plus longue : 287 requêtes,
   **103,8 M tokens relus en cache**, 2,29 M écrits en cache, pour 53,30 $.
2. **Les captures relues dans le contexte** : 18 des 42 lectures de fichiers de cette session portent des images ;
   ce sont elles qui gonflent le contexte (leur part exacte en tokens n'est pas mesurable par le script actuel).
3. **La durée de vie des sessions** : 49 sessions = **2 493,64 $** (moyenne 50,89 $, max 191,49 $).

## Ce que le kit prendrait en charge
- Une mémoire par chantier (`.story/state/<chantier>.md`, première ligne « Next: ») et la liste de ce qui attend
  le propriétaire (`.story/awaits.md`), au lieu de les reconstituer en relisant les passations.
- Les leçons et les problèmes dans `.story/`, avec un statut.
- Les hooks de démarrage et d'arrêt qui affichent et rappellent l'état.
- Plus tard, si utile : un tableau (board) pour les commentaires du propriétaire.

## Ce qu'il ne doit pas toucher
- Les directives de CLAUDE.md de l'app.
- Le protocole de chantier visuel : captures avant/après aux deux tailles, une vague à la fois, points d'arrêt où
  le propriétaire regarde et dit de continuer.
- Les commandes de vérification (`npm run banc`, `temoins`, `build`, `garde`, `frontiere`, `guichets`, `entretien`).
- Les documents de passation du projet, **qui restent la source de vérité** : `.story/` les résume et y renvoie,
  sans les remplacer.

## Import automatique (dépôt + transcriptions, corrigé par le second passage)
Presque tout se reconstruit sans les chats, parce que ce projet passe la main **dans le dépôt**.

**9 chantiers** (le premier passage en avait 8 : 6 justes, 1 périmé, 1 faux ; 1 manquait) :
- `revamp-heure-bleue` : montrer au propriétaire les captures v4 et obtenir son feu vert pour la vague suivante.
- `resultats` : reprendre le backlog des résultats, un chantier étroit avec captures avant/après.
- `entretien` (corrigé) : l'ingestion est close ; regard du propriétaire sur les leçons pilotes et 2 points d'audit, puis production en série.
- `serre` : phase 4 ; la phase 5 dort ; l'armement attend la clé du propriétaire.
- `landing` (corrigé) : vagues suivantes ordonnées, d'abord le verdict du propriétaire sur la cinématique au téléphone.
- `soil-glsl` : le propriétaire choisit la prochaine vague.
- `persistance` : dormant par décision du propriétaire.
- `mobile-adaptation` : une branche d'un commit destinée à un autre projet ; le propriétaire décide.
- `arbres` (ajouté) : galerie d'arbres 3D, en attente du choix de photos de référence.

**Ce qui attend le propriétaire** (8 points) :
- Regarder les captures v4 de la refonte et dire « continuer » ou « ajuster ».
- Choisir la prochaine vague du labo du sol.
- Trancher les 7 branches non fusionnées et la PR #4 encore ouverte.
- Archiver les 8 sessions « bloquées » dont le travail a été fusionné.
- Dire si les variables de base de l'environnement cloud sont celles de l'app.
- Donner le verdict sur la cinématique du landing au téléphone.
- Choisir les photos de référence des arbres.
- Regarder les leçons pilotes de l'Entretien et les 2 points d'audit.
