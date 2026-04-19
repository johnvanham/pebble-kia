// Clay configuration schema. Opens in the Pebble mobile app (or the
// emulator's browser) when the user picks "Settings" on the app's entry
// in the locker. Stored values live in localStorage keyed by messageKey.

module.exports = [
  {
    type: 'heading',
    defaultValue: 'pebble-kia'
  },
  {
    type: 'text',
    defaultValue:
      'Point the watchapp at your self-hosted proxy. See ' +
      'github.com/johnvanham/pebble-kia for setup.'
  },
  {
    type: 'section',
    items: [
      {
        type: 'heading',
        defaultValue: 'Proxy'
      },
      {
        type: 'input',
        messageKey: 'PROXY_URL',
        defaultValue: 'http://localhost:8000',
        label: 'Base URL',
        attributes: {
          placeholder: 'https://kia.example.com',
          type: 'url',
          autocorrect: 'off',
          autocapitalize: 'off',
          spellcheck: 'false'
        }
      },
      {
        type: 'input',
        messageKey: 'PROXY_TOKEN',
        defaultValue: '',
        label: 'Bearer token',
        attributes: {
          type: 'password',
          autocorrect: 'off',
          autocapitalize: 'off',
          spellcheck: 'false'
        }
      }
    ]
  },
  {
    type: 'submit',
    defaultValue: 'Save'
  }
];
