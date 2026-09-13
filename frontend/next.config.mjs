/** @type {import('next').NextConfig} */
const nextConfig = {
  // `standalone`: o `next build` gera em .next/standalone um pacote minimo
  // (server.js + so os node_modules que o runtime importa). E o que
  // frontend/Dockerfile.prod copia para a imagem final — sem isto a imagem
  // carregaria o projeto inteiro, node_modules de desenvolvimento incluidos.
  // Nao muda nada no `next dev`.
  output: "standalone",
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "http2.mlstatic.com",
      },
    ],
  },
}

export default nextConfig
