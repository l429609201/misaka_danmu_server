## React + Vite + TailwindCSS template

Create your own project based on this template by running:

```bash
npx degit quavedev/react-vite-tailwindcss-template your-project
```

## Install

```bash
npm install
```

> You can make sure you are running Node.js 18 with `nvm`. Run `nvm use` to set the Node.js 18 in this project.

## Develop

```bash
npm run dev
```

## Build

```bash
npm build
```

Preview:

```bash
npm run preview
```

## Deploy on zCloud

Create your account at [zcloud.ws](https://zcloud.ws).

Connect to your GitHub, create your app and done!

Push to Deploy.

Or use the preconfigured [action](./.github/workflows/deploy.yaml) to deploy your app. Uncomment all the lines and add your zCloud User Token (ZCLOUD_USER_TOKEN) to your secrets on GitHub and replace `env` placeholder value with your app env name (check it on app.zcloud.ws > Your app env > Settings).
