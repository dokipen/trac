<?xml version="1.0"?>
<!-- RSS generated by Trac v<?cs var:$trac.version ?> on <?cs var:$trac.time ?> -->
<rss version="2.0">
   <?cs set base_url = $HTTP.Protocol+$HTTP.Host ?>
   <?cs def:rss_item(category,title, link, descr) ?>
      <item>
        <author><?cs var:$item.author ?></author>
        <pubDate><?cs var:$item.datetime ?></pubDate>
        <title><?cs var:$title ?></title>	  
        <link><?cs var:$base_url ?><?cs var:$link ?></link>
        <description><?cs var:$descr ?></description>
        <category><?cs var:$category ?></category>
      </item>
   <?cs /def ?>
    <channel>
      <?cs if $project.name ?>
        <title><?cs var:$project.name?>: <?cs var:$title ?></title>
      <?cs else ?>
        <title><?cs var:$title ?></title>
      <?cs /if ?>
      <link><?cs var:$base_url ?><?cs var:$trac.href.timeline ?></link>
      <description>Trac Timeline</description>
      <language>en-us</language>
      <generator>Trac v<?cs var:$trac.version ?></generator>
      <image>
        <title><?cs var:$project.name?></title>
        <url><?cs if !$header_logo.src_abs ?><?cs var:$base_url ?><?cs /if ?><?cs var $header_logo.src ?></url>
        <link><?cs var:$base_url ?><?cs var:$trac.href.timeline ?></link>
      </image>
      <?cs each:item = $timeline.items ?>
        <?cs if:item.type == #1 ?><!-- Changeset -->
          <?cs call:rss_item('Changeset',
                             'Changeset ['+$item.idata+']: '+$item.shortmsg, 
                             $item.href, $item.msg_nowiki) ?>
        <?cs elif:item.type == #2 ?><!-- New ticket -->
          <?cs call:rss_item('Ticket',
                             'Ticket #'+$item.idata+' created: '+$item.shortmsg,
                             $item.href, $item.message) ?>
        <?cs elif:item.type == #3 ?><!-- Closed ticket -->
          <?cs call:rss_item('Ticket',
                             'Ticket #'+$item.idata+' resolved: '+$item.shortmsg,
                             $item.href, $item.message) ?>
        <?cs elif:item.type == #4 ?><!-- Reopened ticket -->
          <?cs call:rss_item('Ticket',
                             '#'+$item.idata+' reopened: '+$item.shortmsg,
                             $item.href, $item.message) ?>
        <?cs elif:item.type == #5 ?><!-- Wiki change -->
          <?cs call:rss_item('Wiki',
                             $item.tdata+" page edited.",
                             $item.href,
'Wiki page <a href="'+$base_url+$item.wiki_href+'">'+$item.tdata+'</a> edited by '+$item.author) ?>
        <?cs elif:item.type == #6 ?><!-- Milestones -->
          <?cs call:rss_item('Milestone',
                             'Milestone ' + $item.message + ' reached.',
                             '',
	       'Milestone ' + $item.tdata + ' reached.') ?>
        <?cs /if ?>
      <?cs /each ?>
    </channel>
</rss>
