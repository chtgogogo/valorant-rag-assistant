# 前端镜像：Node 构建 + Nginx 托管静态文件并反代 /api 到后端
# ------------------------------------------------------------
# 构建阶段：vite build 产物进 nginx；运行阶段：/api 反代到 backend:8001
# （容器网络内走服务名，不经宿主端口）
# ------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
