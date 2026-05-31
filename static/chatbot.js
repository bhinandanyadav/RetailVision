(function(){
  const BASE_URL = '';
  function el(html){ const d=document.createElement('div'); d.innerHTML=html.trim(); return d.firstChild; }

  function formatReply(text) {
    let safe = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    safe = safe.replace(/`([^`]+)`/g, '<span class="code-inline">$1</span>');
    safe = safe.replace(/\n/g, '<br>');
    return safe;
  }

  const widget = el(`
    <div id="chat-widget">
      <button id="chat-toggle" aria-label="Open AI assistant">✦</button>
      <div id="chat-modal" aria-hidden="true">
        <div class="chat-header">
          <div class="chat-header-left">
            <div class="chat-avatar">✦</div>
            <div class="chat-header-info">
              <h4>AI Assistant</h4>
              <span>Online</span>
            </div>
          </div>
          <div class="chat-header-actions">
            <button id="chat-close" class="chat-header-btn" aria-label="Close">✕</button>
          </div>
        </div>
        <div id="chat-messages" class="chat-messages">
          <div class="chat-msg bot">
            <div class="chat-msg-content">Hi! I'm your AI assistant. Ask me about models, heatmaps, or analytics.</div>
          </div>
        </div>
        <form id="chat-form" class="chat-form">
          <input id="chat-input" class="chat-input" placeholder="Ask anything..." autocomplete="off" />
          <button type="submit" class="chat-send" aria-label="Send">➤</button>
        </form>
      </div>
    </div>
  `);

  document.addEventListener('DOMContentLoaded', ()=>{
    document.body.appendChild(widget);
    const toggle = document.getElementById('chat-toggle');
    const modal = document.getElementById('chat-modal');
    const close = document.getElementById('chat-close');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('chat-input');
    const messages = document.getElementById('chat-messages');

    function open(){
      modal.style.display='flex';
      modal.setAttribute('aria-hidden','false');
      input.focus();
    }

    function closeModal(){
      modal.style.display='none';
      modal.setAttribute('aria-hidden','true');
    }

    toggle.addEventListener('click', open);
    close.addEventListener('click', closeModal);

    function addMessage(from, text){
      const node = document.createElement('div');
      node.className = 'chat-msg ' + (from === 'user' ? 'user' : 'bot');
      const content = document.createElement('div');
      content.className = 'chat-msg-content';
      if(from === 'user'){
        content.textContent = text;
      } else {
        content.innerHTML = formatReply(text);
      }
      node.appendChild(content);
      messages.appendChild(node);
      messages.scrollTop = messages.scrollHeight;
    }

    form.addEventListener('submit', async (ev)=>{
      ev.preventDefault();
      const text = input.value.trim();
      if(!text) return;
      addMessage('user', text);
      input.value = '';

      const typing = document.createElement('div');
      typing.className = 'chat-msg bot';
      typing.innerHTML = '<div class="chat-msg-content typing-indicator"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>';
      messages.appendChild(typing);
      messages.scrollTop = messages.scrollHeight;

      try{
        const resp = await fetch(BASE_URL + '/chatbot/message', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({message: text})
        });
        const data = await resp.json();
        typing.remove();
        addMessage('bot', data.reply || 'Sorry, no response.');
      }catch(err){
        typing.remove();
        addMessage('bot', 'Network error: ' + err.message);
      }
    });
  });
})();
