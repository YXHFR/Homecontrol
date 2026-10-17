# Skills för Claude Code

Färdiga arbetssätt (skills) som Claude Code kan använda i det här repot. De är kopierade från två öppna källor och inte ändrade, utom namnbytena nedan.

| Källa | Version | Licens |
| --- | --- | --- |
| [mattpocock/skills](https://github.com/mattpocock/skills) | `49dd158` | MIT, se `LICENSES/LICENSE-mattpocock-skills` |
| [michael-denyer/pstack-claude](https://github.com/michael-denyer/pstack-claude) | `7bad1eb` | MIT, se `LICENSES/` (även NOTICE-filerna) |

## Vad som är med

- Alla Matt Pococks skills från `engineering`, `in-progress`, `misc` och `productivity`. Mappen `deprecated` är inte med.
- Alla skills från pstack (`plugins/pstack/skills`).

## Vad som inte är med

- pstacks hook (`SessionStart`), som annars kör ett skript varje gång en session startar. Ägaren valde bort den.
- pstacks egna subagenter och modellinställningar. Bara skills är kopierade.

## Namnbyten

- `tdd` och `teach` finns i båda paketen. pstacks versioner heter här `pstack-tdd` och `pstack-teach`.
- Matts `code-review` heter här `matt-code-review`, så att den inte krockar med Claude Codes inbyggda `code-review`.

## Uppdatera

Klona källorna på nytt, kopiera in mapparna igen och uppdatera versionerna i tabellen. Gör samma namnbyten och lägg inte till någon hook.
