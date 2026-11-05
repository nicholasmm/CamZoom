# CamZoom

Zoom digital para webcam no Windows, aplicado ao vivo em reuniões (Google Meet, Teams, Zoom, Discord e outros).

Webcams de alta resolução (1440p, 4K) costumam mostrar muito do ambiente e deixar você pequeno no meio da imagem. O CamZoom recorta e amplia a área que você escolher e envia o resultado para uma câmera virtual. Nas reuniões, basta selecionar essa câmera.

## Recursos

- Zoom de 1x a 4x, ajustável durante a chamada
- Enquadramento horizontal e vertical
- **Seguir rosto**: o recorte acompanha você automaticamente
- Transições suaves de zoom e posição
- Espelhamento e saída em 720p ou 1080p
- Lembra as configurações ao fechar

## Requisitos

- Windows 10 ou 11
- [OBS Studio](https://obsproject.com/) instalado. O CamZoom usa a **OBS Virtual Camera**, que vem com o OBS. Não é preciso abrir o OBS.

## Como usar

1. Baixe o `CamZoom.exe` na página de [Releases](../../releases/latest) e abra.
2. Escolha a sua webcam e ajuste o enquadramento olhando a pré-visualização.
3. No app de reunião, selecione a câmera **"OBS Virtual Camera"**.
4. Deixe o CamZoom aberto durante a reunião.

> Na primeira execução o Windows pode mostrar "O Windows protegeu o computador", porque o executável não tem assinatura digital. Clique em **Mais informações → Executar assim mesmo**.

### Atalhos

| Ação | Atalho |
|---|---|
| Zoom | `+` / `−` ou roda do mouse sobre a pré-visualização |
| Mover enquadramento | Setas do teclado |
| Centralizar | `C` |
| Ligar/desligar "seguir rosto" | `F` |

## Problemas comuns

**O app de reunião diz que a câmera está em uso ou sem permissão.**
O app está tentando abrir a webcam física, que já está sendo usada pelo CamZoom (o Windows só permite um programa por vez). Troque a câmera do app para **"OBS Virtual Camera"**.

**A reunião mostra a imagem padrão do OBS em vez de mim.**
O CamZoom está fechado ou ainda iniciando. Depois de abrir, ele leva alguns segundos até começar a transmitir.

**O OBS Studio está aberto com a câmera virtual dele ligada.**
Os dois disputam a mesma câmera virtual. Use um ou outro.

## Rodar pelo código-fonte

Requer Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python camzoom.py
```

Para gerar o `.exe`: `.\build.ps1` (o arquivo sai em `dist\CamZoom.exe`).
