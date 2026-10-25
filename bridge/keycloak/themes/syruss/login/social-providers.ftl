<#macro show social>
  <div id="kc-social-providers" class="syruss-social-wrap">
    <ul class="syruss-social">
      <#list social.providers as p>
        <li>
          <a id="social-${p.alias}" class="syruss-social-button" href="${p.loginUrl}" aria-label="${msg("doLogIn")} ${p.displayName}">
            <#if p.alias == "google">
              <svg aria-hidden="true" viewBox="0 0 488 512" width="18" height="18" focusable="false">
                <path fill="currentColor" d="M488 261.8C488 403.3 391.1 504 248 504 110.8 504 0 393.2 0 256S110.8 8 248 8c66.8 0 123 24.5 166.3 64.9l-67.5 64.9C258.5 52.6 94.3 116.6 94.3 256c0 86.5 69.1 156.6 153.7 156.6 98.2 0 135-70.4 140.8-106.9H248v-85.3h236.1c2.3 12.7 3.9 24.9 3.9 41.4z"/>
              </svg>
            </#if>
            <span>Continue with ${p.displayName}</span>
          </a>
        </li>
      </#list>
    </ul>
    <div class="syruss-or" aria-hidden="true"><span>or</span></div>
  </div>
</#macro>
