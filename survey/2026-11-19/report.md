# Sondage de l'étape 0 — rapport 1 (utilisateur de chat seulement)

Date : 2026-11-19 · Langue : français (rapport technique en anglais)
Anonymisation : aucun nom de personne, d'établissement, de fournisseur ni d'identifiant. Les rôles et catégories remplacent les noms.

Source : 15 conversations courtes accessibles (2026-10-14 → 2026-11-16, compte du réviseur) et une longue conversation de travail (2026-10-28 → 2026-11-20 selon ses transcriptions, compactée 8 fois entre le 17 et le 20 novembre). Aucune conversation plus ancienne n'est accessible. Un réviseur indépendant a revu la première passe et corrigé 4 points.

## Portrait

| Partie | Ce qu'il fait | Où ça vit | Copies du même fait | Coût ou friction | Niveau |
|---|---|---|---|---|---|
| Automatisation Microsoft 365 (listes, flux, applications, intranet) | Conçoit et met en production plusieurs processus distincts pour son établissement; Claude agit directement dans l'environnement (extension de navigateur, connecteur Microsoft 365, documents Claude) | Dans l'environnement Microsoft 365 lui-même | État du chantier gardé à la fois dans l'environnement et dans les résumés de compactage | 84 relances « continue » sur 288 messages; 8 compactages en 3 jours | vu |
| Communications de direction | Révision d'un bulletin hebdomadaire dans une conversation récurrente (plusieurs semaines d'éditions), messages au personnel | Pages intranet, brouillons | Règles de style répétées d'une semaine à l'autre | « Comme la dernière fois » demandé 2 fois, plus une demande de surligner ce qui avait déjà paru : Claude n'avait pas la semaine précédente | vu |
| Questions RH et pédagogiques | Avis ponctuels (stages, palmarès, plans d'action) | Réponses dans le chat, documents Claude | — | Faible | vu |
| Bénévolat | Rapport de diagnostic d'une organisation jeunesse | Document Claude | — | Faible | vu |
| Personnel | Finances, démarches administratives, loisirs, actualité | Réponses dans le chat | — | Une lettre rédigée d'abord selon le mauvais droit, puis refaite | vu |
| Outils de développement | Aucun (pas de GitHub, Claude Code, terminal ni clé d'API avant ce sondage) | — | — | Mise en place à faire | vu |

## Les 3 plus grands coûts

1. **Relancer à la main.** Dans la longue conversation, 84 des 288 messages de l'utilisateur (29 %) sont « continue » ou « continuer », et le message médian fait 24 caractères. L'utilisateur sert surtout à relancer Claude après chaque tour limité.
2. **Un contexte rempli par les outils.** (Le réviseur compte environ 308 messages de l'utilisateur dans cette conversation, messages récents compris.) Sur environ 7,1 millions de caractères analysés : résultats d'outils 58,7 %, appels d'outils 31,8 %, réponses de Claude 7,4 %, réflexion 1,9 %, mots de l'utilisateur 0,3 %. La conversation a été compactée 8 fois en 3 jours, chaque fois en résumant l'état du chantier.
3. **Pas de mémoire entre conversations** (jusqu'au 19 novembre). L'utilisateur redemande « comme la dernière fois » ou colle un résumé venu d'une autre IA. Les accords et confirmations reviennent souvent (au moins 24 « oui / ok » et 19 messages d'autorisation).

## Faits gardés en double

- L'état des projets : dans l'environnement Microsoft 365 et dans les résumés de compactage.
- Les règles de travail (étapes courtes, accord avant toute action réelle, langue) : reprises dans chaque résumé.
- Les règles de style du bulletin : redites chaque semaine.

## Ce qu'un kit pourrait prendre en charge

- Un état de projet persistant (le « tableau ») : décisions, identifiants, étapes restantes, en remplacement des résumés de compactage.
- Les préférences permanentes, écrites une seule fois.
- Les longues séquences d'actions sans relance manuelle à chaque tour.

## Ce qu'il ne doit pas toucher

- Les données de production et les renseignements personnels de l'établissement.
- Toute action qui exige l'accord de l'utilisateur : publier, partager, envoyer des courriels réels.
- Les conversations personnelles.

## Langue

Français pour les échanges et les rapports; anglais accepté pour le rapport technique et le code.
