// The abyss theme. `astryx experimental theme editor` wrote this file once and never changes it again.
// The checked settings live in abyss.settings.json and the generated abyss.settings.ts;
// change them with `astryx experimental theme editor set abyss ...`. Add your own changes below: they win over the settings.
import {defineTheme, defineSyntaxTheme} from '@astryxdesign/core/theme';
import {abyssSettings} from './abyss.settings';

/**
 * Always-dark GitHub Dark-inspired syntax palette (WCAG AA >= 4.5:1 against #0d1117)
 * so fenced code blocks, tool outputs, file previews, and diffs stand out crisply
 * from light and dark prose surfaces alike.
 */
const abyssSyntax = defineSyntaxTheme({
  name: 'abyss-github-dark',
  tokens: {
    keyword: '#ff7b72',
    string: '#a5d6ff',
    comment: '#8b949e',
    number: '#79c0ff',
    function: '#d2a8ff',
    type: '#ffa657',
    variable: '#e6edf3',
    operator: '#ff7b72',
    constant: '#79c0ff',
    tag: '#7ee787',
    attribute: '#79c0ff',
    property: '#79c0ff',
    punctuation: '#c9d1d9',
    background: '#0d1117',
    added: 'rgba(46, 160, 67, 0.24)',
    removed: 'rgba(248, 81, 73, 0.24)',
    'diff-header': '#161b22',
    'added-marker': '#56d364',
    'removed-marker': '#ff7b72',
  },
});

export const abyssTheme = defineTheme({
  extends: abyssSettings,
  name: 'abyss',
  syntax: abyssSyntax,
  components: {
    'code-block': {
      base: {
        colorScheme: 'dark',
        backgroundColor: '#0d1117',
        color: '#e6edf3',
        borderWidth: '1px',
        borderStyle: 'solid',
        borderColor: '#30363d',
      },
    },
    'code-block-header': {
      base: {
        backgroundColor: '#161b22',
        borderBottomColor: '#30363d',
        color: '#c9d1d9',
      },
    },
    'code-block-title': {
      base: {
        color: '#c9d1d9',
      },
    },
    'code-block-copy-button': {
      base: {
        color: '#c9d1d9',
      },
    },
    code: {
      base: {
        backgroundColor: 'light-dark(#eef2ff, #1e2538)',
        borderWidth: '1px',
        borderStyle: 'solid',
        borderColor: 'light-dark(#c7d2fe, #3b4768)',
        color: 'light-dark(#3730a3, #a5b4fc)',
      },
    },
  },
});
