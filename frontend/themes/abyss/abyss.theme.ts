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
        borderRadius: '8px',
        fontSize: '0.78125rem',
        lineHeight: '1.5',
        '--spacing-3': '8px',
        '--spacing-4': '12px',
      },
      'size:sm': {
        borderRadius: '6px',
        fontSize: '0.75rem',
        lineHeight: '1.45',
        '--spacing-3': '5px',
        '--spacing-4': '10px',
      },
    },
    'code-block-header': {
      base: {
        backgroundColor: '#161b22',
        borderBottomColor: '#30363d',
        color: '#c9d1d9',
        fontSize: '0.75rem',
        '--spacing-2': '4px',
        '--spacing-4': '12px',
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
        borderRadius: '4px',
        paddingBlock: '1px',
        paddingInline: '5px',
        fontSize: '0.85em',
      },
    },
    'chat-tool-calls': {
      base: {
        marginBlockStart: '0px',
      },
    },
    'chat-message-bubble': {
      'variant:ghost': {
        paddingInline: '0px',
        paddingBlock: '4px',
      },
    },
    'chat-message-metadata': {
      base: {
        marginBlockStart: '2px',
      },
    },
    'collapsible-trigger': {
      base: {
        paddingBlock: '2px',
      },
    },
    'collapsible-content': {
      base: {
        paddingBlockStart: '2px',
        paddingBlockEnd: '4px',
      },
    },
    card: {
      base: {
        borderRadius: '14px',
      },
    },
    'text-input': {
      base: {
        borderRadius: '8px',
      },
      'size:sm': {
        borderRadius: '6px',
      },
    },
    item: {
      selected: {
        backgroundColor: 'light-dark(rgba(15, 23, 42, 0.09), rgba(255, 255, 255, 0.13))',
        '--color-text-secondary': 'light-dark(#334155, #cbd5e1)',
      },
    },
    'kbd-key': {
      base: {
        minWidth: '20px',
        height: '20px',
        borderRadius: '5px',
        fontVariantNumeric: 'tabular-nums',
      },
    },
  },
});
