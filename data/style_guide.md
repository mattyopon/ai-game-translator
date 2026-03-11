# Game Translation Style Guide

## General Rules
- Use natural, fluent English that sounds like it was written by a native speaker
- Keep translations concise — game UI has limited space
- Maintain consistency with established game terminology (see glossary)
- Preserve the original tone: serious text stays serious, humorous text stays humorous
- Do not add information that is not in the original text
- Do not omit information that is in the original text

## Skill/Effect Descriptions
- Use present tense: "Increases ATK by 30%" not "Will increase ATK by 30%"
- Be concise: "Restores 50 HP" not "This skill restores 50 HP to the user"
- Use standard game terms: ATK, DEF, HP, MP, SPD, CRIT
- Percentages: "30%" not "thirty percent"
- Duration: "for 3 turns" or "3 turns"
- Targeting: "all allies", "all enemies", "single target"
- Stacking: "Stacks up to 3 times"
- Conditions: "When HP is below 50%", "At the start of each turn"

## Item Descriptions
- Can be slightly more descriptive than skill effects
- Include flavor text if present in the original
- Keep practical information (stats, effects) clearly readable
- Rarity/quality terms: Common, Uncommon, Rare, Epic, Legendary

## UI Text
- Ultra-concise: fit within button/label constraints
- Use standard UI conventions: "OK", "Cancel", "Confirm", "Settings"
- Menu items: capitalize each word ("Item Shop", "Quest Log", "Party Formation")
- Status labels: "Equipped", "Locked", "New", "Max Level"

## Dialogue
- Match character personality and speech patterns
- Use contractions for casual characters ("don't", "can't")
- Formal characters use full forms ("do not", "cannot")
- Preserve emotional tone and emphasis
- Ellipses: use "..." (three dots) for pauses or trailing off
- Emphasis: use italics markup if supported, otherwise CAPS sparingly

## Numbers and Variables
- Keep all numbers as-is
- Preserve placeholders exactly: {0}, {1}, %s, %d, {{name}}, etc.
- Format: "Deals {0} damage" not "Deals damage of {0}"
- Do not translate variable names inside placeholders
- Maintain placeholder order unless grammar absolutely requires reordering

## Common Pitfalls
- 「〜する」 patterns: translate the action naturally, don't force "do ~" constructions
- Passive voice in Japanese often maps to active voice in English
- 「〜ことができる」: use "can" not "is able to"
- Double negatives in Japanese: simplify to positive statements in English when natural
