FROM node:18 as builder

RUN mkdir -p /workspace/app && chown node:node /workspace -R

USER node:node

WORKDIR /workspace/app

# Pre cache packages
COPY --chown=node:node . /workspace/app

RUN npm install && npm run build

FROM node:18

RUN npm i -g serve

COPY --from=builder --chown=node:node /workspace/app/dist /app

USER node:node

WORKDIR /app

ENTRYPOINT ["serve", "-p", "3000",  "-s", "/app"]
