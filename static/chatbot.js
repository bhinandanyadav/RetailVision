(function(){
  const BASE_URL = '';
  function el(html){ const d=document.createElement('div'); d.innerHTML=html.trim(); return d.firstChild; }

  function formatReply(text) {
    // Escape HTML
    let safe = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'gt;');
    // Convert backtick-wrapped text to styled spans
    safe = safe.replace(/`([^`]+)`/g, '<span class="code-inline">$1</span>');
    // Convert newlines to <br>
    safe = safe.replace(/\n/g, '<br>');
    return safe;
  }

  const widget = el(`
    <div id="chat-widget" class="chat-widget">
      <button id="chat-toggle" class="chat-toggle">Chat</button>
      <div id="chat-modal" class="chat-modal" aria-hidden="true">
        <div class="chat-header">Assistant <button id="chat-close" class="chat-close">×</button></div>
        <div id="chat-messages" class="chat-messages" aria-live="polite"></div>
        <form id="chat-form" class="chat-form">
          <input id="chat-input" class="chat-input" placeholder="Ask about models, heatmaps, or help..." autocomplete="off" />
          <button type="submit" class="chat-send">Send</button>
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

    function open(){ modal.style.display='flex'; modal.setAttribute('aria-hidden','false'); input.focus(); }
    function closeModal(){ modal.style.display='none'; modal.setAttribute('aria-hidden','true'); }

    toggle.addEventListener('click', ()=>{ open(); });
    close.addEventListener('click', ()=>{ closeModal(); });

    function addMessage(from, text){
      const node = document.createElement('div');
      node.className = 'chat-msg ' + (from==='user' ? 'user' : 'bot');
      if(from === 'user'){
        node.textContent = text;
      } else {
        node.innerHTML = formatReply(text);
      }
      messages.appendChild(node);
      messages.scrollTop = messages.scrollHeight;
    }

    form.addEventListener('submit', async (ev)=>{
      ev.preventDefault();
      const text = input.value.trim();
      if(!text) return;
      addMessage('user', text);
      input.value = '';
      try{
        const resp = await fetch(BASE_URL + '/chatbot/message', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({message: text})
        });
        const data = await resp.json();
        addMessage('bot', data.reply || 'Sorry, no response.');
      }catch(err){
        addMessage('bot', 'Network error: '+err.message);
      }
    });
  });
})();