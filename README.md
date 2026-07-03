# ffmpeg-slideshow

Microserviço que monta um vídeo 9:16 (MP4, H.264 + áudio silencioso) a partir
de uma lista de URLs de imagem. Usado para gerar Reels no autoblog do Fica ON Brasil.

## Endpoint
`POST /slideshow`  (header `x-token: ugc_slideshow_7yKp29Qm`)

```json
{ "images": ["https://...1.png", "https://...2.png", "https://...3.png"], "seconds_each": 3 }
```
Retorna o arquivo `reel.mp4` (binário, video/mp4). `GET /health` → `ok`.

## Endpoint /watermark
`POST /watermark` (header `x-token: ugc_slideshow_7yKp29Qm`)

```json
{ "video": "https://...clipe.mp4", "text": "Fica ON Brasil" }
```
Queima o texto (marca) no rodapé do vídeo via ffmpeg `drawtext` (`text` é opcional, default "Fica ON Brasil"). Retorna `watermarked.mp4`.

## Deploy no EasyPanel (mesma stack/projeto do n8n)
1. EasyPanel → projeto onde está o n8n → **+ Create Service** → **App**.
2. Nome do serviço: **ffmpeg-slideshow** (esse nome vira o host interno).
3. Source: **Dockerfile**. Aponte para um repositório Git com estes 2 arquivos
   (`app.py` + `Dockerfile`), ou cole-os via a opção de build por Dockerfile.
4. Porta interna: **8080** (não precisa expor domínio público — o n8n acessa pela rede interna).
5. (Opcional) Variável de ambiente `SLIDESHOW_TOKEN` para trocar o token.
6. Deploy. Teste: dentro da rede, `http://ffmpeg-slideshow:8080/health` deve responder `ok`.

Depois de no ar, me avise o host interno (provavelmente `http://ffmpeg-slideshow:8080`)
que eu ligo o ramo de Reels no autoblog (gera MP4 → sobe no WP → publica Reels IG + FB).
