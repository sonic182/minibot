Demo
====

.. meta::
   :description: Screenshots of Minibot understanding images, summarizing web pages,
      generating charts, and transcribing voice messages in Telegram.

Screenshots of Minibot in action over Telegram.

.. raw:: html

   <style>
     .demo-grid {
       display: grid;
       grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
       gap: 1.5rem;
       margin: 1.5rem 0;
     }
     .demo-card {
       border: 1px solid var(--sy-c-divider, #8883);
       border-radius: 10px;
       padding: 1rem;
       text-align: center;
     }
     .demo-card.demo-card-wide {
       grid-column: 1 / -1;
     }
     .demo-card .demo-card-title {
       font-size: 1.05rem;
       font-weight: 600;
       margin: 0 0 0.4rem;
     }
     .demo-card .demo-card-note {
       font-size: 0.85rem;
       color: var(--sy-c-text-2, #888);
       margin: 0 0 0.75rem;
     }
     .demo-card img {
       max-width: 100%;
       border-radius: 6px;
       display: block;
       margin: 0 auto;
     }
     .demo-card-images {
       display: grid;
       grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
       gap: 0.75rem;
       align-items: start;
     }
   </style>

   <div class="demo-grid">
     <div class="demo-card">
       <p class="demo-card-title">Understand photos sent in chat</p>

.. image:: _static/demo/image_understanding.jpg
   :width: 280px

.. raw:: html

     </div>
     <div class="demo-card">
       <p class="demo-card-title">Browse the web and summarize a page</p>

.. image:: _static/demo/openai_blog_summary.jpg
   :width: 280px

.. raw:: html

     </div>
     <div class="demo-card demo-card-wide">
       <p class="demo-card-title">Fetch data and generate a chart</p>
       <p class="demo-card-note">Uses <code>http_request</code> + <code>python_execute</code></p>
       <div class="demo-card-images">

.. image:: _static/demo/pypi_download_chart_request.jpg
   :width: 320px

.. image:: _static/demo/pypi_download_chart_full.jpg
   :width: 320px

.. raw:: html

       </div>
     </div>
     <div class="demo-card">
       <p class="demo-card-title">Transcribe voice messages</p>
       <p class="demo-card-note">Requires the <code>stt</code> extra: <code>pip install "minibot[stt]"</code></p>

.. image:: _static/demo/voice_message_transcription.jpg
   :width: 280px

.. raw:: html

     </div>
   </div>
